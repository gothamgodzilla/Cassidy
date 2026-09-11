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

"""JSON structured logging for vote events."""

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Optional


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
        }
        for key in ("user_id", "mix_id", "vote_timestamp", "request_id", "event"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger("wingman.os.vote")
    if logger.handlers:
        logger.setLevel(level)
        return logger
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logger


def vote_extra(
    user_id: str,
    mix_id: str,
    vote_timestamp: Optional[str] = None,
    **fields: Any,
) -> dict:
    extra = {
        "user_id": user_id,
        "mix_id": mix_id,
        "vote_timestamp": vote_timestamp or datetime.now(timezone.utc).isoformat(),
    }
    extra.update(fields)
    return extra
