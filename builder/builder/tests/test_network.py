import socket
from unittest.mock import Mock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from builder.network import assert_public_url, open_public_url, read_bounded_bytes


class TestPublicNetwork(FrappeTestCase):
	def test_rejects_private_and_credentialed_urls(self):
		private = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))]
		with patch("builder.network.socket.getaddrinfo", return_value=private):
			with self.assertRaises(frappe.PermissionError):
				assert_public_url("http://example.com/test")
		with self.assertRaises(frappe.PermissionError):
			assert_public_url("https://user:password@example.com/test")

	def test_revalidates_redirect_targets(self):
		response = Mock(status_code=302, headers={"location": "http://127.0.0.1/private"})
		with (
			patch(
				"builder.network.assert_public_url", side_effect=[["93.184.216.34"], frappe.PermissionError()]
			),
			patch("builder.network.pinned_get", return_value=response),
			self.assertRaises(frappe.PermissionError),
		):
			open_public_url("https://example.com/start")
		response.close.assert_called_once()

	def test_stops_chunked_responses_at_the_byte_limit(self):
		response = Mock(headers={}, iter_content=Mock(return_value=[b"1234", b"5678"]))
		with self.assertRaises(frappe.ValidationError):
			read_bounded_bytes(response, 6)

	def test_rejects_an_oversized_content_length_before_reading(self):
		response = Mock(headers={"content-length": "9"})
		with self.assertRaises(frappe.ValidationError):
			read_bounded_bytes(response, 8)
		response.iter_content.assert_not_called()
