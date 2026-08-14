"""
Meridian Servicing Console (mock)
----------------------------------
A deliberately legacy-styled internal banking back-office app used as the
proxy target for this project. Server-rendered, table-based layout, no test
IDs, minimal semantic markup -- meant to resemble the "long tail" legacy
surfaces described in the assignment brief, not a modern SPA.

Not a real bank. All data is fake and in-memory only.
"""
import random
import time
import uuid
from flask import Flask, request, redirect, session, make_response

app = Flask(__name__)
app.secret_key = "dev-only-not-a-real-secret"

# ---------------------------------------------------------------------------
# Fake data store (in-memory, resets on restart)
# ---------------------------------------------------------------------------
MEMBERS = {
    "12345": {
        "name": "Jane A. Doe",
        "checking": 2400.50,
        "savings": 15230.10,
        "status": "active",
        "subaccounts": ["SA-8841 (Holiday Fund) - $612.00"],
    },
    "67890": {
        "name": "Robert T. Kim",
        "checking": 980.00,
        "savings": 4102.75,
        "status": "active",
        "subaccounts": [],
    },
    "99999": {
        "name": "Restricted Record",
        "checking": 0,
        "savings": 0,
        "status": "restricted",   # triggers permission-denied business outcome
        "subaccounts": [],
    },
    "55555": {
        "name": "Server Trouble Test",
        "checking": 0,
        "savings": 0,
        "status": "server_error",  # triggers simulated hard failure (500)
        "subaccounts": [],
    },
    "77777": {
        "name": "Slow Loader Test",
        "checking": 512.20,
        "savings": 88.40,
        "status": "slow",   # triggers transient slowness (recoverable)
        "subaccounts": [],
    },
    "88888": {
        "name": "Session Timeout Test",
        "checking": 1500.00,
        "savings": 3200.00,
        "status": "active",
        "subaccounts": [],
    },
}

# tracks whether the one-time simulated session expiry has already fired,
# per member id -- models a session that expires once and is fine after
# re-authentication, rather than permanently broken
_SESSION_EXPIRED_ONCE = {"88888": False}

NEW_ACCOUNTS = {}  # created sub-accounts, keyed by generated account number


def layout(body, title="Meridian Servicing Console"):
    # Intentionally old-school: nested tables, inline styling, no test ids,
    # no semantic tags beyond what's structurally necessary.
    return f"""<html><head><title>{title}</title></head>
<body bgcolor="#ECECEC">
<table width="100%" cellpadding="4" cellspacing="0" border="0" bgcolor="#003366">
<tr><td><font color="white" size="4"><b>MERIDIAN SERVICING CONSOLE</b></font></td>
<td align="right"><font color="white" size="2">internal use only</font></td></tr>
</table>
<table width="100%" cellpadding="10"><tr><td>
{body}
</td></tr></table>
</body></html>"""


@app.route("/")
def home():
    err = request.args.get("err", "")
    err_html = f'<p><font color="red">{err}</font></p>' if err else ""
    body = f"""
    <h2>Member Lookup</h2>
    {err_html}
    <form method="GET" action="/member/search">
    <table border="0" cellpadding="3">
    <tr><td>Member ID:</td><td><input type="text" name="member_id" size="12"></td>
    <td><input type="submit" value="Search"></td></tr>
    </table>
    </form>
    """
    return layout(body)


@app.route("/member/search")
def member_search():
    raw_id = request.args.get("member_id", "").strip()

    if not raw_id.isdigit():
        return redirect(f"/?err=Invalid+member+ID+format.+Digits+only.")

    member = MEMBERS.get(raw_id)
    if not member:
        return redirect(f"/?err=No+member+found+with+ID+{raw_id}.")

    return redirect(f"/member/{raw_id}")


@app.route("/member/<member_id>")
def member_detail(member_id):
    member = MEMBERS.get(member_id)
    if not member:
        return redirect(f"/?err=No+member+found+with+ID+{member_id}.")

    if member["status"] == "server_error":
        # Hard failure: simulated internal server error
        resp = make_response(layout(
            "<h2>Internal Server Error</h2><p>The application encountered an "
            "unexpected condition (ref: MSC-500-TEST). Please contact support.</p>"
        ), 500)
        return resp

    if member["status"] == "restricted":
        body = """<h2>Access Denied</h2>
        <p>This record requires elevated servicing permissions that your
        current role does not have. Request escalated access to proceed.</p>
        <p><i>[permission_denied]</i></p>"""
        return layout(body)

    if member["status"] == "slow":
        time.sleep(2.5)  # transient slowness a replay should tolerate/retry

    # `?simulate=timeout` forces the session-expired branch regardless of
    # prior request history -- makes the recoverable-condition demo
    # repeatable without needing to restart the app between runs.
    force_timeout = request.args.get("simulate") == "timeout"

    if force_timeout or (member_id == "88888" and not _SESSION_EXPIRED_ONCE["88888"]):
        _SESSION_EXPIRED_ONCE["88888"] = True
        body = f"""<h2>Session Expired</h2>
        <p>Your session has timed out. Please re-authenticate to continue.</p>
        <p><a href="/member/{member_id}">Re-authenticate</a></p>
        <p><i>[session_timeout]</i></p>"""
        return layout(body)

    sub_rows = "".join(f"<tr><td colspan=2>{s}</td></tr>" for s in member["subaccounts"]) \
        or "<tr><td colspan=2><i>none</i></td></tr>"

    body = f"""
    <h2>Member Record: {member_id}</h2>
    <table border="1" cellpadding="5" cellspacing="0">
    <tr><td><b>Name</b></td><td>{member['name']}</td></tr>
    <tr><td><b>Checking Balance</b></td><td>${member['checking']:.2f}</td></tr>
    <tr><td><b>Savings Balance</b></td><td>${member['savings']:.2f}</td></tr>
    </table>
    <h3>Sub-Accounts</h3>
    <table border="1" cellpadding="5" cellspacing="0">
    {sub_rows}
    </table>
    <p><a href="/member/{member_id}/new-subaccount">Open New Sub-Account</a></p>
    <p><a href="/">Back to search</a></p>
    """
    return layout(body)


@app.route("/member/<member_id>/new-subaccount", methods=["GET"])
def new_subaccount_form(member_id):
    member = MEMBERS.get(member_id)
    if not member:
        return redirect(f"/?err=No+member+found+with+ID+{member_id}.")

    err = request.args.get("err", "")
    err_html = f'<p><font color="red">{err}</font></p>' if err else ""

    body = f"""
    <h2>Open New Sub-Account for {member_id}</h2>
    {err_html}
    <form method="POST" action="/member/{member_id}/new-subaccount">
    <table border="0" cellpadding="3">
    <tr><td>Account Nickname:</td><td><input type="text" name="nickname" size="20"></td></tr>
    <tr><td>Account Type:</td><td>
        <select name="account_type">
        <option value="savings">Savings</option>
        <option value="holiday">Holiday Club</option>
        <option value="goal">Goal Saver</option>
        </select>
    </td></tr>
    <tr><td>Initial Deposit ($):</td><td><input type="text" name="deposit" size="10"></td></tr>
    <tr><td colspan="2"><input type="submit" value="Continue"></td></tr>
    </table>
    </form>
    """
    return layout(body)


@app.route("/member/<member_id>/new-subaccount", methods=["POST"])
def new_subaccount_submit(member_id):
    member = MEMBERS.get(member_id)
    if not member:
        return redirect(f"/?err=No+member+found+with+ID+{member_id}.")

    nickname = request.form.get("nickname", "").strip()
    account_type = request.form.get("account_type", "")
    deposit_raw = request.form.get("deposit", "").strip()

    try:
        deposit = float(deposit_raw)
    except ValueError:
        return redirect(
            f"/member/{member_id}/new-subaccount?err=Deposit+must+be+a+number."
        )

    if deposit < 25:
        return redirect(
            f"/member/{member_id}/new-subaccount?err=Minimum+opening+deposit+is+$25.00."
        )

    if not nickname:
        return redirect(
            f"/member/{member_id}/new-subaccount?err=Nickname+is+required."
        )

    # stash pending request in session, require explicit confirmation step
    # -- this models the "risky/irreversible action" checkpoint in the brief
    session["pending_subaccount"] = {
        "member_id": member_id,
        "nickname": nickname,
        "account_type": account_type,
        "deposit": deposit,
    }
    return redirect(f"/member/{member_id}/new-subaccount/confirm")


@app.route("/member/<member_id>/new-subaccount/confirm", methods=["GET"])
def new_subaccount_confirm(member_id):
    pending = session.get("pending_subaccount")
    if not pending or pending["member_id"] != member_id:
        return redirect(f"/member/{member_id}/new-subaccount")

    body = f"""
    <h2>Confirm New Sub-Account</h2>
    <table border="1" cellpadding="5">
    <tr><td>Member</td><td>{member_id}</td></tr>
    <tr><td>Nickname</td><td>{pending['nickname']}</td></tr>
    <tr><td>Type</td><td>{pending['account_type']}</td></tr>
    <tr><td>Initial Deposit</td><td>${pending['deposit']:.2f}</td></tr>
    </table>
    <p><b>This action creates a real ledger entry and cannot be undone from
    this screen.</b></p>
    <form method="POST" action="/member/{member_id}/new-subaccount/confirm">
    <input type="submit" name="decision" value="Confirm and Open">
    <input type="submit" name="decision" value="Cancel">
    </form>
    """
    return layout(body)


@app.route("/member/<member_id>/new-subaccount/confirm", methods=["POST"])
def new_subaccount_finalize(member_id):
    pending = session.get("pending_subaccount")
    decision = request.form.get("decision", "")

    if not pending or pending["member_id"] != member_id:
        return redirect(f"/member/{member_id}/new-subaccount")

    if decision != "Confirm and Open":
        session.pop("pending_subaccount", None)
        return redirect(f"/member/{member_id}")

    acct_no = f"SA-{random.randint(1000,9999)}"
    MEMBERS[member_id]["subaccounts"].append(
        f"{acct_no} ({pending['nickname']}) - ${pending['deposit']:.2f}"
    )
    NEW_ACCOUNTS[acct_no] = pending
    session.pop("pending_subaccount", None)

    body = f"""
    <h2>Sub-Account Opened Successfully</h2>
    <p>New account number: <b>{acct_no}</b></p>
    <p>Nickname: {pending['nickname']} &nbsp; Type: {pending['account_type']}
    &nbsp; Opening Deposit: ${pending['deposit']:.2f}</p>
    <p><a href="/member/{member_id}">Return to member record</a></p>
    """
    return layout(body)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5055, debug=False)
