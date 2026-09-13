from types import SimpleNamespace
from unittest.mock import patch

from frappe.tests.utils import FrappeTestCase

from builder.ai.agent.artifact import brief_image_parts, image_url_resolves, reference_geometry

PUBLIC = "https://cdn.example.com/hero.png"
PRIVATE = "https://internal.example.com/hero.png"


class Response:
	def __init__(self, status_code=200):
		self.status_code = status_code
		self.ok = status_code < 400
		self.closed = False

	def close(self):
		self.closed = True


def responding(status_code=200):
	response = Response(status_code)
	return patch("builder.ai.agent.artifact.open_public_url", return_value=(response, PUBLIC))


def blocking():
	return patch("builder.ai.agent.artifact.open_public_url", side_effect=Exception("private"))


class TestImageUrlResolves(FrappeTestCase):
	def test_accepts_a_reachable_url(self):
		with responding(200):
			self.assertTrue(image_url_resolves(PUBLIC))

	def test_rejects_a_missing_url(self):
		with responding(404):
			self.assertFalse(image_url_resolves(PUBLIC))

	def test_accepts_a_successful_redirect_result(self):
		with responding(302):
			self.assertTrue(image_url_resolves(PUBLIC))

	def test_rejects_a_private_url(self):
		with blocking():
			self.assertFalse(image_url_resolves(PRIVATE))

	def test_closes_the_probe_response(self):
		response = Response(200)
		with patch("builder.ai.agent.artifact.open_public_url", return_value=(response, PUBLIC)):
			image_url_resolves(PUBLIC)

		self.assertTrue(response.closed)


class TestBriefImageParts(FrappeTestCase):
	def test_attaches_a_reachable_marker(self):
		with responding(200):
			parts = brief_image_parts(f"HERO IMAGE: {PUBLIC}")

		self.assertEqual(parts, [{"type": "image_url", "image_url": {"url": PUBLIC}}])

	def test_skips_a_private_marker(self):
		with blocking():
			parts = brief_image_parts(f"REFERENCE IMAGE: {PRIVATE}")

		self.assertEqual(parts, [])

	def test_stops_at_the_attachment_limit(self):
		brief = "\n".join(f"HERO IMAGE: https://cdn.example.com/{i}.png" for i in range(4))
		with responding(200):
			parts = brief_image_parts(brief)

		self.assertEqual(len(parts), 2)


class TestReferenceGeometry(FrappeTestCase):
	def test_no_reads_yields_nothing(self):
		self.assertEqual(reference_geometry(SimpleNamespace(reference_reads=[])), "")

	def test_single_read_stays_unlabelled(self):
		out = reference_geometry(SimpleNamespace(reference_reads=["Page A: geometry"]))
		self.assertIn("Page A: geometry", out)
		self.assertNotIn("PRIMARY REFERENCE", out)

	def test_first_read_is_primary_and_the_rest_secondary(self):
		out = reference_geometry(SimpleNamespace(reference_reads=["Page A: geometry", "Page B: geometry"]))
		self.assertLess(out.index("PRIMARY REFERENCE"), out.index("Page A: geometry"))
		self.assertLess(out.index("SECONDARY REFERENCE"), out.index("Page B: geometry"))
		self.assertEqual(out.count("PRIMARY REFERENCE"), 1)
