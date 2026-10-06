#!/usr/bin/env python3
"""Fetch Cursor usage/spend data via Admin API or dashboard session."""

from __future__ import annotations

import argparse
import json
import os
import sys
from calendar import monthrange
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

from session_auth import build_session_cookie, get_bearer_token, resolve_session_token

ADMIN_API_URL = "https://api.cursor.com/teams/spend"
DASHBOARD_BASE = "https://cursor.com"
API2_BASE = "https://api2.cursor.sh"
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT = SCRIPT_DIR / "data" / "latest.json"


def load_config() -> dict[str, str]:
    env_path = SCRIPT_DIR / "config.env"
    if env_path.exists():
        load_dotenv(env_path)
    else:
        load_dotenv()

    return {
        "api_key": os.getenv("CURSOR_API_KEY", "").strip(),
        "email": os.getenv("CURSOR_EMAIL", "").strip(),
        "auth_mode": os.getenv("AUTH_MODE", "auto").strip().lower(),
    }


def add_one_month(dt: datetime) -> datetime:
    year = dt.year + (1 if dt.month == 12 else 0)
    month = 1 if dt.month == 12 else dt.month + 1
    day = min(dt.day, monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def compute_reset_date(cycle_start_ms: int | float | None) -> dict[str, str | int | None]:
    if not cycle_start_ms:
        return {"reset_date": None, "days_until_reset": None, "cycle_start": None}

    start = datetime.fromtimestamp(cycle_start_ms / 1000, tz=timezone.utc).astimezone()
    reset = add_one_month(start)
    now = datetime.now().astimezone()
    days = max(0, (reset.date() - now.date()).days)
    return {
        "reset_date": reset.strftime("%b %d, %Y"),
        "days_until_reset": days,
        "cycle_start": start.strftime("%b %d, %Y"),
    }


def pick_member(team_member_spend: list[dict], email: str) -> dict | None:
    target = email.lower()
    for member in team_member_spend:
        if str(member.get("email", "")).lower() == target:
            return member
    return None


def build_admin_summary(raw: dict, member: dict, email: str) -> dict:
    spend_cents = float(member.get("spendCents") or 0)
    included_spend_cents = float(member.get("includedSpendCents") or 0)
    overall_spend_cents = float(member.get("overallSpendCents") or 0)

    if overall_spend_cents == 0 and included_spend_cents:
        overall_spend_cents = included_spend_cents + spend_cents

    reset_info = compute_reset_date(raw.get("subscriptionCycleStart"))

    return {
        "auth_source": "admin_api",
        "fetched_at": datetime.now().astimezone().isoformat(),
        "email": email,
        "name": member.get("name") or email,
        "current_date": datetime.now().astimezone().strftime("%b %d, %Y %H:%M"),
        "spend_dollars": round(spend_cents / 100, 2),
        "included_spend_dollars": round(included_spend_cents / 100, 2),
        "overall_spend_dollars": round(overall_spend_cents / 100, 2),
        "auto_percent_used": member.get("autoPercentUsed"),
        "api_percent_used": member.get("apiPercentUsed"),
        "total_percent_used": member.get("totalPercentUsed"),
        "monthly_limit_dollars": member.get("monthlyLimitDollars"),
        "billing_tier": member.get("billingTier"),
        "reset_date": reset_info["reset_date"],
        "days_until_reset": reset_info["days_until_reset"],
        "cycle_start": reset_info["cycle_start"],
        "subscription_cycle_start_ms": raw.get("subscriptionCycleStart"),
        "raw_member": member,
    }


def _first_number(*values: object) -> float | None:
    for value in values:
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _cycle_start_ms(
    period_usage: dict,
    usage_summary: dict | None,
    legacy_usage: dict | None,
) -> int | None:
    for candidate in (
        (usage_summary or {}).get("billingCycleStart"),
        period_usage.get("billingCycleStart"),
        (legacy_usage or {}).get("startOfMonth"),
    ):
        if candidate is None or candidate == "":
            continue
        if isinstance(candidate, (int, float)):
            return int(candidate)
        text = str(candidate)
        if text.isdigit():
            return int(text)
        try:
            return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() * 1000)
        except ValueError:
            continue
    return None


def build_session_summary(
    me: dict,
    period_usage: dict,
    usage_summary: dict | None,
    legacy_usage: dict | None,
) -> dict:
    email = me.get("email") or ""
    name = me.get("name") or email

    cycle_start_ms = _cycle_start_ms(period_usage, usage_summary, legacy_usage)
    reset_info = compute_reset_date(cycle_start_ms)

    plan_usage = period_usage.get("planUsage") or {}
    spend_limit_usage = period_usage.get("spendLimitUsage") or {}
    summary = usage_summary or {}
    individual_usage = summary.get("individualUsage") or {}
    plan_from_summary = individual_usage.get("plan") or {}
    on_demand_from_summary = individual_usage.get("onDemand") or {}
    team_on_demand = ((summary.get("teamUsage") or {}).get("onDemand") or {})

    # Prefer /api/usage-summary for enterprise/team accounts. GetCurrentPeriodUsage
    # often returns zeros even when real usage exists in usage-summary.
    plan_breakdown = plan_from_summary.get("breakdown") or {}
    included_spend_cents = _first_number(
        plan_breakdown.get("included"),
        plan_usage.get("includedSpend"),
        plan_from_summary.get("used"),
        plan_usage.get("totalSpend"),
    ) or 0.0

    bonus_spend_cents = _first_number(
        plan_breakdown.get("bonus"),
        plan_usage.get("bonusSpend"),
    )

    included_limit_cents = _first_number(
        plan_from_summary.get("limit"),
        plan_usage.get("limit"),
    )

    # Personal on-demand only — never fall back to team pool numbers here.
    personal_on_demand_cents = _first_number(
        on_demand_from_summary.get("used"),
        spend_limit_usage.get("individualUsed"),
        summary.get("onDemandSpendCents"),
        summary.get("spendCents"),
    ) or 0.0

    personal_on_demand_limit_cents = _first_number(
        on_demand_from_summary.get("limit"),
        spend_limit_usage.get("individualLimit"),
    )

    team_on_demand_cents = _first_number(
        team_on_demand.get("used"),
        spend_limit_usage.get("pooledUsed"),
    )
    team_on_demand_limit_cents = _first_number(
        team_on_demand.get("limit"),
        spend_limit_usage.get("pooledLimit"),
    )

    auto_percent = _first_number(
        plan_from_summary.get("autoPercentUsed"),
        plan_usage.get("autoPercentUsed"),
    )
    api_percent = _first_number(
        plan_from_summary.get("apiPercentUsed"),
        plan_usage.get("apiPercentUsed"),
    )
    total_percent = _first_number(
        plan_from_summary.get("totalPercentUsed"),
        plan_usage.get("totalPercentUsed"),
    )

    personal_limit_dollars = (
        round(personal_on_demand_limit_cents / 100, 2)
        if personal_on_demand_limit_cents is not None
        else None
    )
    included_limit_dollars = (
        round(included_limit_cents / 100, 2) if included_limit_cents is not None else None
    )
    team_limit_dollars = (
        round(team_on_demand_limit_cents / 100, 2)
        if team_on_demand_limit_cents is not None
        else None
    )
    team_spend_dollars = (
        round(team_on_demand_cents / 100, 2) if team_on_demand_cents is not None else None
    )
    bonus_spend_dollars = (
        round(bonus_spend_cents / 100, 2) if bonus_spend_cents is not None else None
    )

    return {
        "auth_source": "dashboard_session",
        "fetched_at": datetime.now().astimezone().isoformat(),
        "email": email,
        "name": name,
        "current_date": datetime.now().astimezone().strftime("%b %d, %Y %H:%M"),
        "spend_dollars": round(personal_on_demand_cents / 100, 2),
        "included_spend_dollars": round(included_spend_cents / 100, 2),
        "included_limit_dollars": included_limit_dollars,
        "bonus_spend_dollars": bonus_spend_dollars,
        "overall_spend_dollars": round(
            (included_spend_cents + personal_on_demand_cents) / 100, 2
        ),
        "auto_percent_used": auto_percent,
        "api_percent_used": api_percent,
        "total_percent_used": total_percent,
        "monthly_limit_dollars": personal_limit_dollars,
        "personal_on_demand_limit_dollars": personal_limit_dollars,
        "team_spend_dollars": team_spend_dollars,
        "team_limit_dollars": team_limit_dollars,
        "billing_tier": period_usage.get("billingTier") or summary.get("membershipType"),
        "reset_date": reset_info["reset_date"],
        "days_until_reset": reset_info["days_until_reset"],
        "cycle_start": reset_info["cycle_start"],
        "subscription_cycle_start_ms": cycle_start_ms,
        "raw_member": {
            "period_usage": period_usage,
            "usage_summary": usage_summary,
            "legacy_usage": legacy_usage,
        },
    }


def fetch_admin_usage(api_key: str, email: str) -> dict:
    response = requests.post(
        ADMIN_API_URL,
        auth=(api_key, ""),
        headers={"Content-Type": "application/json"},
        json={"searchTerm": email, "pageSize": 25, "page": 1},
        timeout=30,
    )

    if response.status_code == 401:
        raise PermissionError("admin_api_unauthorized")
    if response.status_code == 429:
        raise RuntimeError("429 Too Many Requests: rate limit exceeded. Try again later.")
    response.raise_for_status()

    raw = response.json()
    members = raw.get("teamMemberSpend") or []
    member = pick_member(members, email)
    if member is None:
        raise RuntimeError(
            f"No spend data found for {email}. Response had {len(members)} member(s)."
        )

    return {"summary": build_admin_summary(raw, member, email), "raw": raw}


def dashboard_headers(cookie: str) -> dict[str, str]:
    return {
        "Cookie": cookie,
        "Content-Type": "application/json",
        "Origin": DASHBOARD_BASE,
        "Referer": f"{DASHBOARD_BASE}/dashboard",
    }


def dashboard_get(cookie: str, path: str) -> dict:
    response = requests.get(
        f"{DASHBOARD_BASE}{path}",
        headers=dashboard_headers(cookie),
        timeout=30,
    )
    if response.status_code == 401:
        raise PermissionError("dashboard_session_unauthorized")
    response.raise_for_status()
    return response.json()


def api2_post(bearer_token: str, path: str, payload: dict | None = None) -> dict:
    response = requests.post(
        f"{API2_BASE}{path}",
        headers={
            "Authorization": f"Bearer {bearer_token}",
            "Content-Type": "application/json",
            "Connect-Protocol-Version": "1",
        },
        json=payload or {},
        timeout=30,
    )
    if response.status_code == 401:
        raise PermissionError("dashboard_session_unauthorized")
    response.raise_for_status()
    return response.json()


def fetch_session_usage(email_hint: str = "") -> dict:
    token = resolve_session_token()
    bearer = get_bearer_token(token)
    cookie = build_session_cookie(token)

    me = dashboard_get(cookie, "/api/auth/me")
    user_id = me.get("id")
    if user_id is None:
        raise RuntimeError("Dashboard session is valid but /api/auth/me returned no user id.")

    email = email_hint or me.get("email") or ""

    period_usage = api2_post(
        bearer,
        "/aiserver.v1.DashboardService/GetCurrentPeriodUsage",
        {},
    )

    usage_summary = None
    try:
        usage_summary = dashboard_get(cookie, "/api/usage-summary")
    except requests.HTTPError:
        usage_summary = None

    legacy_usage = None
    try:
        legacy_usage = dashboard_get(cookie, f"/api/usage?user={user_id}")
    except requests.HTTPError:
        legacy_usage = None

    summary = build_session_summary(me, period_usage, usage_summary, legacy_usage)
    if email and summary["email"] and email.lower() != summary["email"].lower():
        raise RuntimeError(
            f"Configured CURSOR_EMAIL ({email}) does not match signed-in account "
            f"({summary['email']})."
        )

    return {
        "summary": summary,
        "raw": {
            "me": me,
            "period_usage": period_usage,
            "usage_summary": usage_summary,
            "legacy_usage": legacy_usage,
        },
    }


def admin_key_looks_like_user_key(api_key: str) -> bool:
    try:
        response = requests.get(
            "https://api.cursor.com/v1/me",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15,
        )
        return response.status_code == 200
    except requests.RequestException:
        return False


def fetch_usage(output_path: Path) -> dict:
    config = load_config()
    auth_mode = config["auth_mode"] or "auto"
    api_key = config["api_key"]
    email = config["email"]

    errors: list[str] = []

    if auth_mode in {"auto", "admin"} and api_key:
        try:
            if not email and auth_mode == "admin":
                raise ValueError("CURSOR_EMAIL is required for AUTH_MODE=admin.")
            if not email:
                raise ValueError("CURSOR_EMAIL is required when using the Admin API.")
            payload = fetch_admin_usage(api_key, email)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            return payload
        except PermissionError:
            if auth_mode == "admin":
                if admin_key_looks_like_user_key(api_key):
                    raise RuntimeError(
                        "401 Unauthorized: your key works with /v1/me but not /teams/spend. "
                        "That is a User/Agent API key. Create a Teams Admin API key in "
                        "cursor.com/dashboard → API Keys (admin:* scope), or set AUTH_MODE=session "
                        "to use your signed-in Cursor app session instead."
                    ) from None
                raise RuntimeError(
                    "401 Unauthorized: invalid Admin API key or insufficient team permissions."
                ) from None
            errors.append("Admin API rejected the configured API key.")
        except Exception as exc:  # noqa: BLE001
            if auth_mode == "admin":
                raise
            errors.append(f"Admin API failed: {exc}")

    if auth_mode in {"auto", "session"}:
        try:
            payload = fetch_session_usage(email)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            return payload
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Dashboard session failed: {exc}")

    message = "Unable to fetch Cursor usage data."
    if errors:
        message += " " + " ".join(errors)
    if api_key and admin_key_looks_like_user_key(api_key):
        message += (
            " Your CURSOR_API_KEY appears to be a User/Agent key (works on /v1/me only). "
            "Use a Teams Admin API key, or set AUTH_MODE=session and sign in to the Cursor app."
        )
    else:
        message += " Sign in to the Cursor app, or set CURSOR_SESSION_TOKEN."
    raise RuntimeError(message)


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch Cursor usage data")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Path to write JSON output",
    )
    args = parser.parse_args()

    try:
        payload = fetch_usage(args.output)
        print(json.dumps(payload["summary"], indent=2))
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI entrypoint
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
