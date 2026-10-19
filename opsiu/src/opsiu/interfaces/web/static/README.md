# Local review assets

`review.css` belongs to OPSIU. The review interface uses system fonts and serves
all assets locally; it makes no CDN requests and needs no JavaScript build.

`htmx.min.js` is the unmodified distribution from **HTMX 2.0.4**. The pinned
[official package metadata](https://raw.githubusercontent.com/bigskysoftware/htmx/v2.0.4/package.json)
identifies version `2.0.4` and license `0BSD`.

- Asset source: [official distribution](https://raw.githubusercontent.com/bigskysoftware/htmx/v2.0.4/dist/htmx.min.js)
- Asset size: `50917` bytes
- Asset SHA-256: `e209dda5c8235479f3166defc7750e1dbcd5a5c1808b7792fc2e6733768fb447`
- License source: [official Zero-Clause BSD license](https://raw.githubusercontent.com/bigskysoftware/htmx/v2.0.4/LICENSE)
- License copy: `htmx.LICENSE` (unmodified, `642` bytes)
- License SHA-256: `d3d2456f76414f2456104660ebd65aff1c04cd7966b942bdabd63f3cdb316a38`

The template configuration disables expression evaluation, inserted script tags,
external-origin HTMX requests, and HTMX's injected indicator stylesheet. The local
stylesheet supplies request indicators. Error responses can replace the decision
panel, and ordinary HTML forms support browsers without JavaScript.

To verify the vendored bytes from the product directory:

```sh
sha256sum src/opsiu/interfaces/web/static/htmx.min.js src/opsiu/interfaces/web/static/htmx.LICENSE
```

When upgrading HTMX, retrieve both files from the same official release, update
these hashes and sizes, and verify both enhanced and ordinary form submissions.
