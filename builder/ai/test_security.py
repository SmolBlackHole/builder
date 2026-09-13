from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from builder.ai.agent.tools.memory import forget_memory, memory_context, save_fact
from builder.ai.api import import_provider_models, save_ai_provider


def website_manager() -> str:
	user = frappe.get_doc(
		{
			"doctype": "User",
			"email": f"builder-security-{frappe.generate_hash(length=8)}@example.test",
			"first_name": "Builder",
			"last_name": "Security",
			"send_welcome_email": 0,
		}
	).insert(ignore_permissions=True)
	user.add_roles("Website Manager")
	return user.name


class TestMemoryIsolation(FrappeTestCase):
	def tearDown(self):
		frappe.set_user("Administrator")
		super().tearDown()

	def test_memories_are_private_to_their_owner(self):
		first_user = website_manager()
		second_user = website_manager()
		secret = f"private-memory-{frappe.generate_hash(length=8)}"

		frappe.set_user(first_user)
		memory_id = save_fact(secret).split("[", 1)[1].split("]", 1)[0]
		self.assertIn(secret, memory_context())

		frappe.set_user(second_user)
		self.assertNotIn(secret, memory_context())
		self.assertTrue(forget_memory(memory_id).startswith("FAILED"))

		frappe.set_user(first_user)
		self.assertTrue(forget_memory(memory_id).startswith("Forgot"))


class TestProviderSecurity(FrappeTestCase):
	def tearDown(self):
		frappe.set_user("Administrator")
		super().tearDown()

	def new_provider(self) -> str:
		return save_ai_provider(
			{
				"provider_name": f"Security {frappe.generate_hash(length=8)}",
				"api_base": "https://api.example.com/v1",
				"api_key": "original-secret",
			}
		)

	def test_only_system_managers_can_manage_providers(self):
		user = website_manager()
		frappe.set_user(user)

		with self.assertRaises(frappe.PermissionError):
			save_ai_provider({"provider_name": "Forbidden Provider"})

	def test_endpoint_changes_require_the_key_again(self):
		provider = self.new_provider()

		with self.assertRaises(frappe.ValidationError):
			save_ai_provider({"api_base": "https://other.example.com/v1"}, provider)

		save_ai_provider(
			{"api_base": "https://other.example.com/v1", "api_key": "replacement-secret"}, provider
		)
		self.assertEqual(
			frappe.get_doc("Builder AI Provider", provider).get_password("api_key"),
			"replacement-secret",
		)

	def test_model_import_does_not_follow_redirects_and_uses_the_stored_key(self):
		provider = self.new_provider()
		response = b'{"data":[{"id":"chat-model"}]}'

		with patch(
			"builder.ai.api.fetch_public_bytes", return_value=(response, {}, "https://api.example.com")
		) as fetch:
			result = import_provider_models(provider)

		self.assertEqual(result["added"], ["chat-model"])
		self.assertEqual(fetch.call_args.kwargs["max_redirects"], 0)
		self.assertEqual(fetch.call_args.kwargs["headers"]["Authorization"], "Bearer original-secret")

	def test_model_import_rejects_private_provider_addresses(self):
		provider = self.new_provider()
		save_ai_provider({"api_base": "http://127.0.0.1:8000", "api_key": "replacement-secret"}, provider)

		with self.assertRaises(frappe.ValidationError):
			import_provider_models(provider)
