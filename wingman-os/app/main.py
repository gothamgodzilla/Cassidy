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

"""FastAPI application factory for the Mangasm+ Live Mix Voting Engine.

Run locally with ``uvicorn app.main:app --port 8080`` (requires ``SUPABASE_URL`` and ``SUPABASE_KEY``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import COMPONENT_NAME, SERVICE_NAME, VERSION
from app.config import Settings
from app.errors import register_error_handlers
from app.logging_config import configure_logging
from app.routers import health, vote
from app.services.voting import VotingService
from app.supabase_client import SupabaseClient


def create_app(settings: Settings | None = None, supabase_client: SupabaseClient | None = None) -> FastAPI:
    """Build the application.

    ``settings`` / ``supabase_client`` are injectable so tests can run against a fake Supabase
    without touching environment variables or the network.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        resolved = settings or Settings()
        configure_logging(resolved.log_level)
        client = supabase_client or SupabaseClient(resolved)
        app.state.settings = resolved
        app.state.supabase = client
        app.state.voting_service = VotingService(client, rankings_limit=resolved.rankings_limit)
        try:
            yield
        finally:
            await client.aclose()

    app = FastAPI(
        title=f"{SERVICE_NAME} / {COMPONENT_NAME}",
        version=VERSION,
        description="Atomic, duplicate-safe voting for Mangasm+ Friday & Saturday live mixes.",
        lifespan=lifespan,
    )
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(vote.router)
    return app


app = create_app()
