# Copyright (c) 2023, Frappe Technologies Pvt Ltd and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase

from builder.builder.patches.disable_restricted_editor_scripts import execute


class TestBuilderSettings(FrappeTestCase):
	def test_restricted_editor_scripts_are_disabled(self):
		frappe.db.set_single_value(
			"Builder Settings",
			"execute_block_scripts_in_editor",
			"Restricted",
		)

		execute()

		self.assertEqual(
			frappe.db.get_single_value("Builder Settings", "execute_block_scripts_in_editor"),
			"Don't Execute",
		)

	def test_unrestricted_editor_scripts_remain_explicitly_enabled(self):
		frappe.db.set_single_value(
			"Builder Settings",
			"execute_block_scripts_in_editor",
			"Unrestricted",
		)

		execute()

		self.assertEqual(
			frappe.db.get_single_value("Builder Settings", "execute_block_scripts_in_editor"),
			"Unrestricted",
		)
