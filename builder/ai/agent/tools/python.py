"""run_python — the general read primitive.

The named read tools answer the common questions; this answers the rest. One
sandboxed snippet can count, aggregate, and cross-reference anything on the
site, so the agent orients itself on demand instead of depending on pre-baked
context or a bespoke tool per question. Frappe's server-script sandbox
(safe_exec) bounds what the code can reach; on top of it, raw SQL and direct
value writes are stripped from the namespace, and a savepoint rollback erases
anything a snippet nevertheless wrote — read-only by construction, not by
convention.
"""

import frappe

from builder.ai.agent.registry import Tool
from builder.utils import (
	get_cached_doc_as_dict,
	get_doc_as_dict,
	safe_count,
	safe_exists,
	safe_get_list,
	safe_get_single_value,
)

RESULT_LIMIT = 6000
SAVEPOINT = "builder_ai_run_python"

# Beyond safe_exec's own rules: no raw SQL (server scripts may read any table,
# including auth internals — Bob's users aren't script authors) and no direct
# value writes. Commit/rollback are already blocked via restrict_commit_rollback.
STRIPPED_DB_METHODS = ("sql", "set_value", "add_index")


def sandbox_globals() -> dict:
	from frappe.utils.safe_exec import get_safe_globals

	safe = get_safe_globals()  # built per call — rebinding keys touches only this copy
	fr = safe["frappe"]
	db = fr["db"]
	sandbox_get_doc = fr["get_doc"]
	for method in STRIPPED_DB_METHODS:
		db.pop(method, None)
	# Server-script globals read with ELEVATED rights (get_all and frappe.db.*
	# skip permission checks) — right for System-Manager-authored scripts, wrong
	# for model-written ones. Every read here answers with the session user's OWN
	# permissions: Bob sees exactly what the person driving it could open in Desk.
	fr["get_all"] = fr["get_list"] = db["get_all"] = db["get_list"] = safe_get_list
	fr["get_doc"] = lambda doctype, name=None: (
		sandbox_get_doc(doctype)
		if name is None and isinstance(doctype, dict)
		else get_doc_as_dict(doctype, name)
	)
	fr["get_cached_doc"] = get_cached_doc_as_dict
	db["get_value"] = safe_get_value
	db["get_single_value"] = safe_get_single_value
	db["exists"] = safe_exists
	db["count"] = safe_count
	if "get_value" in fr:
		fr["get_value"] = safe_get_value
	return safe


def safe_get_value(doctype, filters, fieldname="name", **kwargs):
	kwargs.pop("for_update", None)
	as_dict = kwargs.pop("as_dict", False)
	fields = list(fieldname) if isinstance(fieldname, list | tuple) else [fieldname]
	rows = safe_get_list(doctype, filters=filters, fields=fields, limit=1, **kwargs)
	if not rows:
		return None
	if as_dict:
		return rows[0]
	if isinstance(fieldname, list | tuple):
		return tuple(rows[0].get(field) for field in fields)
	return rows[0].get(fieldname)


def run_python(ctx, args: dict) -> str:
	from frappe.utils.safe_exec import is_safe_exec_enabled, safe_exec

	script = (args.get("script") or "").strip()
	if not script:
		return "FAILED: pass `script` — Python that assigns the answer to `result`."
	if not is_safe_exec_enabled():
		return "FAILED: the script sandbox is disabled on this bench — use the other read tools."
	_locals = {"page_id": ctx.page_id, "result": None}
	frappe.db.savepoint(SAVEPOINT)
	try:
		# Audited dynamic execution — the point of this tool. Server-script sandbox,
		# raw SQL and value-writes stripped (sandbox_globals), commits blocked, and
		# the savepoint rollback below discards anything a snippet wrote.
		safe_exec(script, sandbox_globals(), _locals, restrict_commit_rollback=True)  # nosemgrep
	except Exception as e:
		return f"FAILED: {type(e).__name__}: {e}"
	finally:
		# This tool READS. A write that slipped past the sandbox vanishes here.
		frappe.db.rollback(save_point=SAVEPOINT)
	result = _locals.get("result")
	if result is None:
		return "Ran, but `result` was never assigned — set result = <the answer> and run again."
	out = result if isinstance(result, str) else frappe.as_json(result)
	if len(out) > RESULT_LIMIT:
		out = out[:RESULT_LIMIT] + "… (truncated — narrow the query)"
	return out


run_python_tool = Tool(
	name="run_python",
	side="server",
	handler=run_python,
	description=(
		"Figure out ANYTHING about this site with a short READ-ONLY Python snippet, run "
		"server-side in the script sandbox. Assign the answer to `result`. This is your "
		"general fallback whenever no other tool answers directly: counts and aggregates, "
		"which page owns a route, the site's own URL (frappe.utils.get_url()), any setting "
		"or record, cross-doctype questions. Available: frappe.get_all/get_list/get_doc, "
		"frappe.db.get_value/get_single_value/count/exists, frappe.utils date/format "
		"helpers, frappe.session.user, and `page_id` (the open page). No imports, no raw "
		"SQL; nothing it writes persists, and reads answer with the current user's own "
		"permissions. Orient yourself with it instead of guessing or asking the user."
	),
	parameters={
		"type": "object",
		"properties": {
			"script": {
				"type": "string",
				"description": "Python statements; assign the answer to `result`.",
			},
		},
		"required": ["script"],
	},
)

TOOLS = [run_python_tool]
