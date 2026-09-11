# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.services.voting import is_active_plus

FUTURE = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
PAST = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()


@pytest.mark.parametrize(
    "subscription, expected",
    [
        ({"tier": "plus", "status": "active", "current_period_end": FUTURE}, True),
        ({"tier": "plus", "status": "trialing", "current_period_end": FUTURE}, True),
        ({"tier": "PLUS", "status": "Active", "current_period_end": None}, True),
        ({"tier": "mangasm_plus", "status": "active", "current_period_end": FUTURE.replace("+00:00", "Z")}, True),
        ({"tier": "plus", "status": "active", "current_period_end": PAST}, False),
        ({"tier": "plus", "status": "canceled", "current_period_end": FUTURE}, False),
        ({"tier": "plus", "status": "past_due", "current_period_end": FUTURE}, False),
        ({"tier": "free", "status": "active", "current_period_end": None}, False),
        ({"tier": "plus", "status": "active", "current_period_end": "not-a-date"}, False),
        ({}, False),
    ],
)
def test_is_active_plus(subscription, expected):
    assert is_active_plus(subscription) is expected


def test_settings_require_supabase_url_and_key(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_reject_non_http_url():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, supabase_url="ftp://nope", supabase_key="k")


def test_settings_reject_blank_key():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, supabase_url="https://x.supabase.co", supabase_key="   ")


def test_settings_read_from_environment(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://proj.supabase.co/")
    monkeypatch.setenv("SUPABASE_KEY", "secret")
    monkeypatch.setenv("LOG_LEVEL", "debug")

    settings = Settings(_env_file=None)

    assert settings.supabase_url == "https://proj.supabase.co"
    assert settings.supabase_key.get_secret_value() == "secret"
    assert settings.log_level == "DEBUG"
    assert "secret" not in repr(settings)
