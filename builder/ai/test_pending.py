import json
from types import SimpleNamespace

import frappe
from frappe.tests.utils import FrappeTestCase

from builder.ai.agent import pending


class TestRequestConfirmation(FrappeTestCase):
	def test_persists_the_turns_steps_and_trace_on_the_card(self):
		session = frappe.get_doc({"doctype": "Builder AI Session", "session_user": "Administrator"}).insert()
		ctx = SimpleNamespace(
			session_id=session.name,
			timeline=lambda: [{"id": 0, "kind": "tool", "tool": "generate_page", "status": "done"}],
			trace=[{"round": 0, "tools": [{"name": "generate_page", "args": "{}"}], "text": ""}],
			loop_model="openrouter/test-model",
			emit=lambda *args, **kwargs: None,
		)

		pending.request_confirmation(ctx, "create_doctype", "Create it?", {"name": "X", "fields": [1]})

		message = frappe.get_all(
			"Builder AI Message", filters={"session": session.name}, fields=["status", "metadata_json"]
		)[0]
		meta = json.loads(message.metadata_json)
		self.assertEqual(message.status, "pending_action")
		self.assertEqual(meta["steps"][0]["tool"], "generate_page")
		self.assertEqual(meta["debug"]["trace"][0]["tools"][0]["name"], "generate_page")


class TestPendingActionAuthorization(FrappeTestCase):
	def setUp(self):
		super().setUp()
		self.user = frappe.get_doc(
			{
				"doctype": "User",
				"email": f"builder-manager-{frappe.generate_hash(length=8)}@example.test",
				"first_name": "Builder",
				"last_name": "Manager",
				"send_welcome_email": 0,
			}
		).insert(ignore_permissions=True)
		self.user.add_roles("Website Manager")

	def tearDown(self):
		frappe.set_user("Administrator")
		super().tearDown()

	def test_website_manager_cannot_create_doctypes_or_public_forms(self):
		frappe.set_user(self.user.name)

		with self.assertRaises(frappe.PermissionError):
			pending.authorize_pending_action("create_doctype", {"name": "Forbidden Type"})
		with self.assertRaises(frappe.PermissionError):
			pending.authorize_pending_action("connect_form", {"page_id": "page-x"})

	def test_seed_requires_create_permission_on_the_target(self):
		frappe.set_user(self.user.name)

		with self.assertRaises(frappe.PermissionError):
			pending.authorize_pending_action("seed_sample_data", {"doctype": "User"})

	def test_ai_form_generation_is_disabled_until_explicitly_enabled(self):
		with self.assertRaises(frappe.PermissionError):
			pending.authorize_pending_action("connect_form", {"page_id": "page-x"})
