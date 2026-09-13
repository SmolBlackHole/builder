import frappe


def execute():
	if frappe.db.get_single_value("Builder Settings", "execute_block_scripts_in_editor") == "Restricted":
		frappe.db.set_single_value(
			"Builder Settings",
			"execute_block_scripts_in_editor",
			"Don't Execute",
		)
