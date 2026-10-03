"""Dev seed: `python -m app.seed` — teams, users and three published example skills. Idempotent."""
from __future__ import annotations

import psycopg
from psycopg.rows import dict_row

from .auth import add_membership, ensure_team, issue_token, load_user, upsert_user
from .config import get_settings
from .db import run_migrations
from .models import SkillContent
from .skills import create_skill, publish


def _t(role, label, window, identifier=None, path=None):
    return {"role": role, "label": label, "identifier": identifier, "path": path or [], "window_title": window}


def _step(title, instruction, app, action, expect=None, irreversible=False):
    return {"title": title, "instruction": instruction, "app": app, "action": action, "expect": expect,
            "screenshot_sha256": None, "irreversible": irreversible}


FNOL = {
    "title": "File a first notice of loss for an auto claim",
    "goal": "Open a new auto claim in ClaimCenter from a customer's call and confirm it by email.",
    "apps": ["Google Chrome", "Microsoft Outlook"],
    "prerequisites": ["ClaimCenter adjuster access", "The customer's policy number and date of loss"],
    "inputs": [
        {"name": "policy_number", "description": "Policy to file against", "example": "P-123456"},
        {"name": "loss_date", "description": "Date of the accident (YYYY-MM-DD)", "example": "2026-09-28"},
        {"name": "loss_description", "description": "What happened, in the customer's words",
         "example": "Rear-ended at a red light on Yonge St."},
    ],
    "steps": [
        _step("Open ClaimCenter", "Switch to Google Chrome and open ClaimCenter.", "Google Chrome",
              {"type": "open_url", "url": "https://claimcenter.example.com/cc/ClaimCenter.do"},
              {"window_title_contains": "ClaimCenter", "element_present": None}),
        _step("Start a new claim", "From the Claim menu at the top, click “New Claim”.", "Google Chrome",
              {"type": "click", "target": _t("AXLink", "New Claim", "ClaimCenter", "TabBar-ClaimTab-NewClaim")},
              {"window_title_contains": "Search or Create Policy", "element_present": None}),
        _step("Enter the policy number", "Type the policy number ({{policy_number}}) into “Policy Number”.",
              "Google Chrome", {"type": "type", "text": "{{policy_number}}",
                                "target": _t("AXTextField", "Policy Number", "Search or Create Policy")}),
        _step("Enter the date of loss", "Type the date of loss ({{loss_date}}) into “Loss Date”.", "Google Chrome",
              {"type": "type", "text": "{{loss_date}}", "target": _t("AXTextField", "Loss Date", "Search or Create Policy")}),
        _step("Search for the policy", "Click “Search” and check the insured's name matches the caller.",
              "Google Chrome", {"type": "click", "target": _t("AXButton", "Search", "Search or Create Policy")},
              {"window_title_contains": None, "element_present": {"role": "AXStaticText", "label": "Insured"}}),
        _step("Describe the loss", "Type what happened ({{loss_description}}) into “Description”.", "Google Chrome",
              {"type": "type", "text": "{{loss_description}}", "target": _t("AXTextArea", "Description", "Basic Information")}),
        _step("Submit the claim", "Click “Finish”. This creates the claim and can't be undone.", "Google Chrome",
              {"type": "click", "target": _t("AXButton", "Finish", "Basic Information", "NewClaimWizard-Finish")},
              {"window_title_contains": "Claim #", "element_present": None}, irreversible=True),
        _step("Email the confirmation", "In Outlook, reply to the customer with the new claim number and press Cmd+Return to send.",
              "Microsoft Outlook", {"type": "key", "key": "cmd+return"}, irreversible=True),
    ],
    "tags": ["claims", "fnol", "auto", "onboarding"],
}

UBI_SCORE = {
    "title": "Look up a policyholder's driving score",
    "goal": "Find a customer's latest UBI trip score and the trips that affected it.",
    "apps": ["Google Chrome"],
    "prerequisites": ["Access to the UBI customer portal (read-only is enough)"],
    "inputs": [{"name": "policy_number", "description": "Customer's policy number", "example": "P-778812"}],
    "steps": [
        _step("Open the UBI portal", "In Chrome, go to the UBI customer portal.", "Google Chrome",
              {"type": "open_url", "url": "https://ubi-portal.example.com/customers"},
              {"window_title_contains": "UBI Portal", "element_present": None}),
        _step("Search for the customer", "Type the policy number ({{policy_number}}) into the search box and press Return.",
              "Google Chrome", {"type": "type", "text": "{{policy_number}}",
                                "target": _t("AXSearchField", "Search customers", "UBI Portal", "customerSearch")}),
        _step("Open the customer", "Click the matching customer row.", "Google Chrome",
              {"type": "click", "target": _t("AXRow", "{{policy_number}}", "UBI Portal")}),
        _step("Open the Driving score tab", "Click the “Driving score” tab.", "Google Chrome",
              {"type": "click", "target": _t("AXTab", "Driving score", "Customer")},
              {"window_title_contains": None, "element_present": {"role": "AXStaticText", "label": "Current score"}}),
        _step("Review recent trips", "Scroll to “Recent trips” — hard-braking and night-driving events are flagged in orange.",
              "Google Chrome", {"type": "wait"}),
    ],
    "tags": ["ubi", "telematics", "customer-service"],
}

MFA_RESET = {
    "title": "Reset a colleague's MFA in the Okta admin console",
    "goal": "Clear a locked-out employee's MFA factors so they can enrol a new phone.",
    "apps": ["Google Chrome", "Slack"],
    "prerequisites": ["Okta help-desk admin role", "Identity verified via the help-desk call-back process"],
    "inputs": [{"name": "employee_email", "description": "Work email of the employee", "example": "jane.doe@example.com"}],
    "steps": [
        _step("Open the Okta admin console", "In Chrome, open the Okta admin console.", "Google Chrome",
              {"type": "open_url", "url": "https://example-admin.okta.com/admin/users"},
              {"window_title_contains": "People", "element_present": None}),
        _step("Find the employee", "Type {{employee_email}} into the people search box.", "Google Chrome",
              {"type": "type", "text": "{{employee_email}}", "target": _t("AXSearchField", "Search people", "People")}),
        _step("Open their profile", "Click the employee's name in the results.", "Google Chrome",
              {"type": "click", "target": _t("AXLink", "{{employee_email}}", "People")}),
        _step("Reset multifactor", "Click “More actions” → “Reset multifactor”, select all factors, then click “Reset”. This can't be undone.",
              "Google Chrome", {"type": "click", "target": _t("AXButton", "Reset multifactor", "Profile")},
              irreversible=True),
        _step("Tell the employee", "In Slack, message the employee that they can enrol a new device at next sign-in.",
              "Slack", {"type": "open_app"}),
    ],
    "tags": ["it", "okta", "mfa", "help-desk"],
}


def seed(database_url: str) -> dict:
    run_migrations(database_url)
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        teams = {name: ensure_team(conn, name) for name in ("UBI", "Claims", "IT")}
        admin_id = upsert_user(conn, "dharmik@example.com", "Dharmik", True)
        member_id = upsert_user(conn, "priya@example.com", "Priya Shah", None)
        add_membership(conn, admin_id, teams["UBI"])
        add_membership(conn, admin_id, teams["IT"])
        add_membership(conn, member_id, teams["Claims"])
        admin, member = load_user(conn, admin_id), load_user(conn, member_id)
        created = []
        for owner, content, team, vis in (
            (member, FNOL, "Claims", "team"),
            (admin, UBI_SCORE, "UBI", "org"),
            (admin, MFA_RESET, "IT", "org"),
        ):
            exists = conn.execute(
                "SELECT 1 FROM skills s JOIN skill_versions v ON v.skill_id = s.id "
                "WHERE s.owner_id = %s AND v.content->>'title' = %s LIMIT 1",
                (owner.id, content["title"]),
            ).fetchone()
            if exists:
                continue
            c = SkillContent.model_validate(content).to_json()
            sid = create_skill(conn, owner_id=owner.id, content=c, team_id=teams[team], visibility=vis)
            row = conn.execute(
                "SELECT s.*, NULL::jsonb AS published FROM skills s WHERE id = %s FOR UPDATE", (sid,)
            ).fetchone()
            publish(conn, row, owner, embeddings=get_settings().embed_provider != "none")
            created.append(content["title"])
        token = issue_token(conn, admin_id, get_settings().token_ttl_hours)
    return {"teams": teams, "admin_id": admin_id, "member_id": member_id, "created": created, "admin_token": token}


def main() -> None:
    out = seed(get_settings().database_url)
    print(f"Seeded teams: {', '.join(out['teams'])}")
    print("Users: dharmik@example.com (admin), priya@example.com (member)")
    print(f"Skills created: {out['created'] or 'none (already seeded)'}")
    print(f"Admin dev token: {out['admin_token']}")


if __name__ == "__main__":
    main()
