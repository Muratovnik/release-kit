"""Presentation of independent release facts, including legacy receipts."""


def facts(state: dict) -> dict:
    publication = state["publication"]
    ci = state.get("ci_verdict")
    if ci is None:
        ci = "passed" if state.get("stages", {}).get("ci") == "passed" else "unknown"
    return {
        "tag_state": "pushed"
        if state.get("pushed")
        else "local"
        if state.get("tag_oid")
        else "absent",
        "publication_state": "absent" if publication == "not-pushed" else publication,
        "ci_verdict": ci,
        "acceptance": "accepted"
        if publication == "published" and state["verification"] == "passed" and ci == "passed"
        else "incomplete",
    }


def next_action(state: dict) -> list[str] | None:
    if state.get("outcome") or facts(state)["acceptance"] == "accepted":
        return None
    value = state["plan"]
    if state["publication"] == "published" and state["verification"] != "passed":
        return ["relkit", "release", "verify", value["tag"], "--root", value["root"]]
    if facts(state)["ci_verdict"] == "failed":
        return None
    return ["relkit", "release", "resume", value["tag"], "--publish", "--root", value["root"]]


def summary(state: dict) -> str:
    value = facts(state)
    return (
        f"tag={value['tag_state']}, publication={value['publication_state']}, "
        f"CI={value['ci_verdict']}, artifacts={state['verification']}, "
        f"acceptance={value['acceptance']}, cleanup={state['cleanup']}"
    )
