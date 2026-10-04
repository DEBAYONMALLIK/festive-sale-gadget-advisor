// The health endpoint takes a backend URL from the query string and fetches it server-side. Without a
// strict allowlist that is a server-side request forgery hole: anyone could point the public function
// at an internal address and read the response. These tests guard that boundary.
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { safeBackend, ALLOWED_SUFFIXES } from '../web/api/health.js';

describe('safeBackend accepts legitimate tunnels', () => {
  test('a cloudflare quick tunnel', () => {
    assert.equal(safeBackend('https://brave-lion-run.trycloudflare.com'),
                 'https://brave-lion-run.trycloudflare.com');
  });
  test('trailing path and query are dropped, origin is kept', () => {
    assert.equal(safeBackend('https://x.trycloudflare.com/some/path?a=1'), 'https://x.trycloudflare.com');
  });
  test('every advertised suffix is actually allowed', () => {
    for (const suffix of ALLOWED_SUFFIXES) {
      assert.ok(safeBackend(`https://host${suffix}`), `${suffix} is listed but rejected`);
    }
  });
});

describe('safeBackend refuses anything that could reach private infrastructure', () => {
  const blocked = {
    'plain http': 'http://x.trycloudflare.com',
    'localhost': 'https://localhost:7860',
    'loopback ip': 'https://127.0.0.1',
    'private range 10.x': 'https://10.0.0.5',
    'private range 192.168.x': 'https://192.168.1.1',
    'cloud metadata endpoint': 'https://169.254.169.254',
    'an arbitrary site': 'https://example.com',
    'file protocol': 'file:///etc/passwd',
    'gopher protocol': 'gopher://127.0.0.1',
    'empty string': '',
    'not a url': 'just some text',
  };
  for (const [name, url] of Object.entries(blocked)) {
    test(name, () => assert.equal(safeBackend(url), '', `${url} must be rejected`));
  }

  test('a lookalike domain that merely contains an allowed suffix', () => {
    assert.equal(safeBackend('https://trycloudflare.com.evil.example'), '');
  });
  test('an allowed suffix used as a username in the authority', () => {
    assert.equal(safeBackend('https://x.trycloudflare.com@evil.example'), '');
  });
  test('an absurdly long url', () => {
    assert.equal(safeBackend(`https://${'a'.repeat(400)}.trycloudflare.com`), '');
  });
  test('a non-string input', () => {
    assert.equal(safeBackend(null), '');
    assert.equal(safeBackend(undefined), '');
  });
});
