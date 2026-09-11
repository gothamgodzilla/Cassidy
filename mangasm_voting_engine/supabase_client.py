"""Supabase client and operations for Mangasm+ Live Mix Voting Engine.

Handles communication with Supabase REST API & RPC endpoints with retry logic.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional
import httpx

from mangasm_voting_engine.config import settings

logger = logging.getLogger("mangasm_voting_engine")


class MembershipCheckError(Exception):
    """Raised when user is not a Mangasm+ member or member record is missing."""
    pass


class DuplicateVoteError(Exception):
    """Raised when user has already voted for this mix."""
    pass


class SupabaseServiceError(Exception):
    """Raised when external Supabase call fails after all retries."""
    pass


class ConfigurationError(Exception):
    """Raised when SUPABASE_URL or SUPABASE_KEY are missing."""
    pass


class SupabaseClient:
    def __init__(
        self,
        supabase_url: Optional[str] = None,
        supabase_key: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
        client: Optional[httpx.AsyncClient] = None,
    ):
        self.supabase_url = (supabase_url or settings.supabase_url).rstrip("/")
        self.supabase_key = supabase_key or settings.supabase_key
        self.timeout = timeout or settings.http_timeout_seconds
        self.max_retries = max_retries if max_retries is not None else settings.max_retries
        self._external_client = client

    def _get_headers(self) -> Dict[str, str]:
        if not self.supabase_key:
            raise ConfigurationError("SUPABASE_KEY is required but not configured.")
        return {
            "apikey": self.supabase_key,
            "Authorization": f"Bearer {self.supabase_key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }

    async def _execute_with_retry(
        self,
        method: str,
        endpoint: str,
        json_data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> httpx.Response:
        if not self.supabase_url:
            raise ConfigurationError("SUPABASE_URL is required but not configured.")

        url = f"{self.supabase_url}{endpoint}"
        headers = self._get_headers()
        last_exception: Optional[Exception] = None

        for attempt in range(1, self.max_retries + 1):
            try:
                if self._external_client:
                    response = await self._external_client.request(
                        method,
                        url,
                        headers=headers,
                        json=json_data,
                        params=params,
                        timeout=self.timeout,
                    )
                else:
                    async with httpx.AsyncClient(timeout=self.timeout) as client:
                        response = await client.request(
                            method,
                            url,
                            headers=headers,
                            json=json_data,
                            params=params,
                        )

                # If server error (5xx) or transient 429, retry
                if response.status_code >= 500 or response.status_code == 429:
                    logger.warning(
                        "Supabase call to %s returned %s on attempt %d/%d: %s",
                        endpoint,
                        response.status_code,
                        attempt,
                        self.max_retries,
                        response.text,
                    )
                    if attempt < self.max_retries:
                        await asyncio.sleep(0.1 * (2 ** (attempt - 1)))
                        continue
                    raise SupabaseServiceError(
                        f"Supabase request failed with status {response.status_code}: {response.text}"
                    )

                return response

            except (httpx.RequestError, httpx.TimeoutException) as ex:
                logger.warning(
                    "Network error connecting to Supabase on attempt %d/%d: %s",
                    attempt,
                    self.max_retries,
                    str(ex),
                )
                last_exception = ex
                if attempt < self.max_retries:
                    await asyncio.sleep(0.1 * (2 ** (attempt - 1)))
                    continue

        raise SupabaseServiceError(
            f"External service failure after {self.max_retries} attempts: {last_exception}"
        )

    async def check_membership(self, user_id: str) -> bool:
        """Step 1: Validate user_id has an active Mangasm+ subscription.

        Queries the `subscriptions` or `users` table via Supabase REST API.
        """
        response = await self._execute_with_retry(
            method="GET",
            endpoint="/rest/v1/user_subscriptions",
            params={
                "user_id": f"eq.{user_id}",
                "select": "user_id,status,plan_tier,is_active",
            },
        )

        if response.status_code != 200:
            raise SupabaseServiceError(f"Failed to check membership: {response.text}")

        records: List[Dict[str, Any]] = response.json()
        if not records:
            return False

        # Verify active subscription
        user_record = records[0]
        is_active = user_record.get("is_active", False)
        status = str(user_record.get("status", "")).lower()
        plan_tier = str(user_record.get("plan_tier", "")).lower()

        # Check if active Mangasm+ member
        is_mangasm_plus = "mangasm+" in plan_tier or "plus" in plan_tier
        is_status_valid = status in ("active", "trialing") or is_active is True

        return is_mangasm_plus and is_status_valid

    async def cast_mix_vote_rpc(self, user_id: str, mix_id: str) -> Dict[str, Any]:
        """Step 2, 3, 4, 5: Call Supabase RPC `cast_mix_vote`.

        The RPC atomically:
        - checks if user already voted for this mix (raises duplicate / returns 409 conflict code)
        - records the vote in `mix_votes` table (user_id, mix_id, vote_timestamp)
        - increments the vote count in `mixes` table
        - returns updated vote count and current queue rankings
        """
        payload = {
            "p_user_id": user_id,
            "p_mix_id": mix_id,
        }

        response = await self._execute_with_retry(
            method="POST",
            endpoint="/rest/v1/rpc/cast_mix_vote",
            json_data=payload,
        )

        if response.status_code == 409:
            raise DuplicateVoteError(f"User {user_id} has already voted for mix {mix_id}")

        if response.status_code == 403:
            raise MembershipCheckError("M+ Membership Required")

        if response.status_code != 200:
            # Check response body for duplicate error signature
            err_text = response.text.lower()
            if "already voted" in err_text or "duplicate" in err_text or "unique" in err_text:
                raise DuplicateVoteError(f"User {user_id} has already voted for mix {mix_id}")
            if "membership required" in err_text or "not mangasm+" in err_text:
                raise MembershipCheckError("M+ Membership Required")

            raise SupabaseServiceError(
                f"RPC cast_mix_vote failed with status {response.status_code}: {response.text}"
            )

        data = response.json()
        # Handle custom error structure inside RPC response if returned as JSON
        if isinstance(data, dict):
            if data.get("error") == "duplicate_vote" or data.get("status") == 409:
                raise DuplicateVoteError(data.get("message", "User already voted for this mix"))
            if data.get("error") == "not_member" or data.get("status") == 403:
                raise MembershipCheckError("M+ Membership Required")

        return data
