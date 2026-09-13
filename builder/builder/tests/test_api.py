import io
from contextlib import contextmanager
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from PIL import Image

from builder.api import (
	convert_to_webp,
	create_page_from_template,
	get_template_groups,
	import_remote_assets,
	import_remote_fonts,
)

FONT = "https://cdn.example.com/inter.woff2"


def png_bytes(color="red"):
	buffer = io.BytesIO()
	Image.new("RGB", (4, 4), color).save(buffer, "PNG")
	return buffer.getvalue()


def palette_png_bytes():
	buffer = io.BytesIO()
	Image.new("P", (4, 4)).save(buffer, "PNG")
	return buffer.getvalue()


class Response:
	def __init__(self, content=b"data", content_type="image/png", status=200):
		self.content = content
		self.headers = {"content-type": content_type}
		self.status = status

	def raise_for_status(self):
		if self.status >= 400:
			raise Exception(f"HTTP {self.status}")

	def as_fetch_result(self):
		self.raise_for_status()
		return self.content, self.headers, "https://cdn.example.com/final"


@contextmanager
def serving(*responses):
	results = iter(
		response.as_fetch_result() if isinstance(response, Response) else response for response in responses
	)

	def fetch(*_args, max_bytes, **_kwargs):
		result = next(results)
		if isinstance(result, Exception):
			raise result
		if len(result[0]) > max_bytes:
			raise frappe.ValidationError
		return result

	with patch("builder.api.fetch_public_bytes", side_effect=fetch) as get:
		yield get


class TestImportRemoteAssets(FrappeTestCase):
	def setUp(self):
		self.cdn = f"https://cdn.example.com/{frappe.generate_hash(length=8)}"

	def test_converts_an_image_to_webp(self):
		url = f"{self.cdn}/hero.png"
		with serving(Response(png_bytes())):
			imported = import_remote_assets([url])

		self.assertTrue(imported[url].endswith(".webp"))

	def test_rejects_svg_by_content_type(self):
		url = f"{self.cdn}/logo.svg"
		with serving(Response(b"<svg/>", "image/svg+xml")):
			imported = import_remote_assets([url])

		self.assertEqual(imported, {})

	def test_keeps_gif_by_extension(self):
		url = f"{self.cdn}/loader.gif"
		with serving(Response(b"GIF89a", "application/octet-stream")):
			imported = import_remote_assets([url])

		self.assertTrue(imported[url].endswith(".gif"))

	def test_reuses_the_file_when_the_same_url_is_imported_twice(self):
		url = f"{self.cdn}/hero.png"
		with serving(Response(png_bytes())):
			first = import_remote_assets([url])
		with serving(Response(png_bytes("blue"))):
			second = import_remote_assets([url])

		self.assertEqual(first[url], second[url])

	def test_fetches_a_repeated_url_once(self):
		url = f"{self.cdn}/hero.png"
		with serving(Response(png_bytes())) as get:
			import_remote_assets([url, url, url])

		self.assertEqual(get.call_count, 1)

	def test_skips_urls_that_are_not_http(self):
		imported = import_remote_assets(["/files/local.png", "data:image/png;base64,x", 42])

		self.assertEqual(imported, {})

	def test_leaves_out_a_url_that_fails(self):
		gone, kept = f"{self.cdn}/gone.png", f"{self.cdn}/hero.png"
		with serving(Exception("boom"), Response(png_bytes())):
			imported = import_remote_assets([gone, kept])

		self.assertNotIn(gone, imported)
		self.assertIn(kept, imported)

	def test_leaves_out_an_asset_over_the_size_limit(self):
		with serving(Response(png_bytes())), patch("builder.api.MAX_ASSET_BYTES", 10):
			imported = import_remote_assets([f"{self.cdn}/huge.png"])

		self.assertEqual(imported, {})

	def test_stops_at_the_import_limit(self):
		urls = [f"{self.cdn}/{i}.png" for i in range(3)]
		with serving(*[Response(png_bytes()) for _ in urls]), patch("builder.api.MAX_IMPORTED_ASSETS", 2):
			imported = import_remote_assets(urls)

		self.assertEqual(len(imported), 2)

	def test_converts_an_image_that_is_not_rgb(self):
		url = f"{self.cdn}/palette.png"
		with serving(Response(palette_png_bytes())):
			imported = import_remote_assets([url])

		self.assertTrue(imported[url].endswith(".webp"))

	def test_accepts_a_json_string(self):
		url = f"{self.cdn}/hero.png"
		with serving(Response(png_bytes())):
			imported = import_remote_assets(frappe.as_json([url]))

		self.assertIn(url, imported)


class TestImportRemoteFonts(FrappeTestCase):
	def test_imports_a_font_and_registers_the_family(self):
		with serving(Response(b"font", "font/woff2")):
			imported = import_remote_fonts([{"family": "Inter Import", "url": FONT}])

		self.assertTrue(imported["Inter Import"].endswith(".woff2"))
		self.assertTrue(frappe.db.exists("User Font", {"font_name": "Inter Import"}))

	def test_reuses_a_family_that_already_exists(self):
		with serving(Response(b"font", "font/woff2")):
			first = import_remote_fonts([{"family": "Reused Import", "url": FONT}])
			second = import_remote_fonts([{"family": "Reused Import", "url": FONT}])

		self.assertEqual(first["Reused Import"], second["Reused Import"])

	def test_skips_a_url_that_is_not_a_font(self):
		with serving(Response(png_bytes())):
			imported = import_remote_fonts([{"family": "Not A Font", "url": "https://cdn.example.com/x.png"}])

		self.assertEqual(imported, {})

	def test_skips_entries_missing_a_family_or_url(self):
		imported = import_remote_fonts([{"family": "", "url": FONT}, {"family": "Nameless"}, {}])

		self.assertEqual(imported, {})

	def test_leaves_out_a_font_over_the_size_limit(self):
		with serving(Response(b"font", "font/woff2")), patch("builder.api.MAX_FONT_BYTES", 2):
			imported = import_remote_fonts([{"family": "Too Big Import", "url": FONT}])

		self.assertEqual(imported, {})

	def test_accepts_a_json_string(self):
		fonts = frappe.as_json([{"family": "Json Import", "url": FONT}])
		with serving(Response(b"font", "font/woff2")):
			imported = import_remote_fonts(fonts)

		self.assertIn("Json Import", imported)

	def test_stops_at_the_font_limit(self):
		fonts = [{"family": f"Limit {i}", "url": FONT} for i in range(3)]
		with (
			serving(*[Response(b"font", "font/woff2") for _ in fonts]),
			patch("builder.api.MAX_IMPORTED_FONTS", 2),
		):
			imported = import_remote_fonts(fonts)

		self.assertEqual(len(imported), 2)


class TestConvertToWebp(FrappeTestCase):
	def test_requires_builder_write_permission(self):
		previous_user = frappe.session.user
		try:
			frappe.set_user("Guest")
			with self.assertRaises(frappe.PermissionError):
				convert_to_webp(image_url="https://cdn.example.com/hero.png")
		finally:
			frappe.set_user(previous_user)

	def test_rejects_builder_asset_path_traversal(self):
		with self.assertRaises(frappe.PermissionError):
			convert_to_webp(image_url="/builder_assets/../../private.png")

	def test_fetches_external_images_through_the_bounded_client(self):
		url = "https://cdn.example.com/convert.png"
		with serving(Response(png_bytes())) as fetch:
			file_url = convert_to_webp(image_url=url)

		self.assertTrue(file_url.endswith(".webp"))
		self.assertEqual(fetch.call_args.kwargs["max_bytes"], 12 * 1024 * 1024)
		file = frappe.get_doc("File", frappe.db.get_value("File", {"file_url": file_url}, "name"))
		file.delete()


class TestRemoteTemplates(FrappeTestCase):
	def test_remote_templates_are_disabled_by_default(self):
		self.assertEqual(get_template_groups(), [])
		with self.assertRaises(frappe.PermissionError):
			create_page_from_template("remote-page")
