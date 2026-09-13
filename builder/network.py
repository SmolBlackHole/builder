import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import frappe
import requests
from frappe import _

MAX_REDIRECTS = 3
FETCH_TIMEOUT = 20
USER_AGENT = "FrappeBuilder/1.0"


class SniAdapter(requests.adapters.HTTPAdapter):
	def __init__(self, hostname: str):
		self.hostname = hostname
		super().__init__()

	def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
		pool_kwargs["server_hostname"] = self.hostname
		super().init_poolmanager(connections, maxsize, block, **pool_kwargs)


def assert_public_url(url: str) -> list[str]:
	parsed = urlparse(url)
	if parsed.scheme not in ("http", "https"):
		frappe.throw(_("Only HTTP/HTTPS URLs are allowed."), frappe.PermissionError)
	if parsed.username or parsed.password:
		frappe.throw(_("Credentials in external URLs are not allowed."), frappe.PermissionError)
	hostname = parsed.hostname
	if not hostname:
		frappe.throw(_("Invalid URL: missing hostname."), frappe.ValidationError)

	try:
		addr_infos = socket.getaddrinfo(hostname, parsed.port)
	except socket.gaierror:
		frappe.throw(_("Could not resolve hostname: {0}").format(hostname), frappe.ValidationError)

	ips = []
	for addr_info in addr_infos:
		ip = ipaddress.ip_address(addr_info[4][0])
		if not ip.is_global:
			frappe.throw(
				_("Requests to private or internal addresses are not allowed."), frappe.PermissionError
			)
		ips.append(str(ip))
	return list(dict.fromkeys(ips))


def pinned_url(url: str, ip: str) -> tuple[str, str]:
	parsed = urlparse(url)
	literal = f"[{ip}]" if ":" in ip else ip
	netloc = f"{literal}:{parsed.port}" if parsed.port else literal
	hostname = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
	host_header = f"{hostname}:{parsed.port}" if parsed.port else hostname
	return parsed._replace(netloc=netloc).geturl(), host_header


def pinned_get(url: str, ip: str, *, params=None, headers=None, timeout=FETCH_TIMEOUT):
	parsed = urlparse(url)
	target, host_header = pinned_url(url, ip)
	session = requests.Session()
	session.trust_env = False
	if parsed.scheme == "https":
		session.mount("https://", SniAdapter(parsed.hostname))
	request_headers = {"User-Agent": USER_AGENT, **(headers or {}), "Host": host_header}
	return session.get(
		target,
		params=params,
		timeout=timeout,
		stream=True,
		allow_redirects=False,
		headers=request_headers,
	)


def open_public_url(
	url: str, *, params=None, headers=None, timeout=FETCH_TIMEOUT, max_redirects=MAX_REDIRECTS
):
	for _redirect_count in range(max_redirects + 1):
		ips = assert_public_url(url)
		response = pinned_get(url, ips[0], params=params, headers=headers, timeout=timeout)
		location = response.headers.get("location")
		if response.status_code in (301, 302, 303, 307, 308) and location:
			response.close()
			url = urljoin(url, location)
			params = None
			headers = None
			continue
		return response, url
	frappe.throw(_("Too many redirects while fetching an external URL."), frappe.ValidationError)


def read_bounded_bytes(response, max_bytes: int) -> bytes:
	content_length = response.headers.get("content-length")
	if content_length and int(content_length) > max_bytes:
		frappe.throw(_("External response exceeds the allowed size."), frappe.ValidationError)

	chunks = []
	total = 0
	for chunk in response.iter_content(chunk_size=64 * 1024, decode_unicode=False):
		if not chunk:
			continue
		total += len(chunk)
		if total > max_bytes:
			frappe.throw(_("External response exceeds the allowed size."), frappe.ValidationError)
		chunks.append(chunk)
	return b"".join(chunks)


def fetch_public_bytes(
	url: str,
	*,
	max_bytes: int,
	params=None,
	headers=None,
	timeout=FETCH_TIMEOUT,
	max_redirects=MAX_REDIRECTS,
):
	response, final_url = open_public_url(
		url, params=params, headers=headers, timeout=timeout, max_redirects=max_redirects
	)
	try:
		response.raise_for_status()
		content = read_bounded_bytes(response, max_bytes)
		return content, response.headers, final_url
	finally:
		response.close()


def fetch_public_data(url: str, *, max_bytes: int, params=None):
	content, headers, _ = fetch_public_bytes(url, max_bytes=max_bytes, params=params)
	text = content.decode("utf-8", errors="replace")
	content_type = headers.get("content-type", "").lower()
	if "json" in content_type:
		return frappe.parse_json(text)
	return text
