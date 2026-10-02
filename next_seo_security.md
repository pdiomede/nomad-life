# Next steps: security and SEO for nomadlife.pro

What is left from the URL Reporter scan of 2 October 2026 (grade B, 72/100) and the SEO review. The app side is done in 1.11.0:
- a Permissions-Policy header;
- `/.well-known/security.txt`;
- `Retry-After` on 429 answers;
- link previews for `/docs`;
- an Organization and a breadcrumb in the structured data;
- full length snippets.

Everything below is done outside the code, in Cloudflare, Hostinger, the server and Google.

## Security (DNS and email)

Most important first.

### 1. CAA record (high)

Only the certificate authorities you name may issue certificates for the domain.

- Cloudflare: **DNS → Records → Add record**.
- Type `CAA`, name `@`, flags `0`, tag `issue` (Only allow specific hostnames), value `letsencrypt.org`.
- Optional: a second CAA record with tag `iodef` and value `mailto:info@nomadlife.pro`, to hear about refused requests.
- If the Cloudflare proxy (orange cloud) is turned on later, Cloudflare adds the CAA records of its own certificate authorities automatically.

### 2. DKIM (do this before DMARC)

The scan found no DKIM key. Cloudflare's import usually misses the DKIM records when the nameservers move from Hostinger.

1. In Hostinger hPanel, open **Emails → your domain → DNS settings** (or "Manage DNS records") and find the DKIM records. They are usually CNAMEs whose name ends in `._domainkey`.
2. Add the same records in Cloudflare DNS. Set the proxy to **DNS only** (grey cloud).
3. Check that mail to and from `@nomadlife.pro` still works.
4. Check the DKIM status in hPanel, or with https://mxtoolbox.com/dkim.aspx

### 3. DMARC (medium)

Once DKIM works, change the TXT record `_dmarc`:

```
v=DMARC1; p=quarantine; rua=mailto:info@nomadlife.pro
```

- Read the reports that arrive at `info@nomadlife.pro` for a few weeks. If only Hostinger shows up as a sender and it passes, move to `p=reject`.
- This affects only mail sent **as** `@nomadlife.pro`. If `GMAIL_USER` in the server's `config.env` is a `@gmail.com` address, the app's emails are not affected.

### 4. SPF (info)

Today it is `v=spf1 include:_spf.mail.hostinger.com ~all`. Once Hostinger is your only sender and DMARC reports are clean, tighten it to `-all`. This is optional: with DMARC on `p=reject` it changes little.

### 5. DNSSEC (medium)

1. Cloudflare: **DNS → Settings → Enable DNSSEC**, then copy the DS record it shows (key tag, algorithm, digest type, digest).
2. Hostinger hPanel: **Domains → nomadlife.pro → DNS / Nameservers → DNSSEC**, then add that DS record.
3. After a few hours, check it at https://dnssec-analyzer.verisignlabs.com/nomadlife.pro

### 6. CDN in front ("No CDN/WAF detected", medium)

- Turn on the Cloudflare proxy (orange cloud) for `nomadlife.pro` and `www`, following **setup_vps.md, section 8, "Optional: Cloudflare in front"**:
  - SSL mode Full (strict);
  - Rocket Loader and Email Address Obfuscation off;
  - the nginx real IP script, so sign up IPs and limits keep seeing the visitor.
- This also answers "Response not cacheable at the edge". Static files already have a 7 day cache and a version in their URL; pages are not cached on purpose.

### 7. HSTS preload (low): wait

Preloading needs `includeSubDomains; preload` and means every subdomain must serve HTTPS forever, mail subdomains included. Getting off the list takes months. Revisit once the domain and its subdomains are settled.

### 8. "No rate-limit headers" (low)

Done where it matters: every "Too many attempts" answer (HTTP 429) carries `Retry-After` since 1.11.0. Rate limit headers on normal pages would only tell attackers how much is left.

### 9. internet.nl

Run https://internet.nl/site/nomadlife.pro/ and https://internet.nl/mail/nomadlife.pro/ once the steps above are done. They check DNSSEC, IPv6, TLS and mail security in one go.

## SEO

The app already has what Google reads:
- a unique title and description per page;
- canonical links;
- `sitemap.xml` and `robots.txt`;
- JSON-LD (WebSite, WebApplication with offers, Organization with logo, and a breadcrumb on `/docs`);
- Open Graph and Twitter images;
- `noindex` on every private page;
- a mobile layout;
- `lang="en"`.

What is left matters more than any tag.

### 1. Google Search Console

1. Go to https://search.google.com/search-console and **Add property → Domain**, then enter `nomadlife.pro`.
2. Copy the TXT record it gives and add it in Cloudflare DNS (name `@`). Then press **Verify**.
3. Go to **Sitemaps** and submit `https://nomadlife.pro/sitemap.xml`.
4. Under **URL inspection**, request indexing for `https://nomadlife.pro/` and `https://nomadlife.pro/docs`.
5. Bing Webmaster Tools: https://www.bing.com/webmasters, then **Import from Google Search Console**. This also feeds DuckDuckGo and others.

### 2. Server settings

- `APP_BASE_URL=https://nomadlife.pro` in the server's `config.env`. Canonical links, the sitemap, link previews and `security.txt` are all built from it.
- `www` must redirect to the main domain. Check it:

  ```bash
  curl -sI https://www.nomadlife.pro | head -3
  ```

  It should answer `301` with `location: https://nomadlife.pro/`. If not, include `www.nomadlife.pro` in the certificate (`sudo certbot --nginx -d nomadlife.pro -d www.nomadlife.pro`) and add a `server` block that returns `301 https://nomadlife.pro$request_uri` for `www`.

### 3. Content (the strongest lever)

The domain is a few days old: Google takes weeks to months to trust it. Pages that answer what people search for bring most of the traffic. Ideas for public guide pages (each one indexable, in the sitemap, linked from the landing page and `/docs`):

- How the 183 day rule works, and why it is not the whole story of tax residency.
- How to count days per country (arrival and departure days, overlapping stays), with the app's rules as the worked example.
- Which documents prove where you lived: rental contracts, hotel bills, flight tickets.
- A digital nomad tax residency checklist for the end of the year.

### 4. Links from other sites

- Product Hunt launch.
- Nomad communities: Reddit r/digitalnomad and r/IWantOut, Nomad List, nomad Facebook groups. Share it as "I built this"; avoid plain ads.
- Indie and SaaS directories (BetaList, Indie Hackers, AlternativeTo).

### 5. Speed (Core Web Vitals count in ranking)

- Host the Inter and Outfit fonts from the app itself instead of Google Fonts: one fewer connection to another site before the page can draw, and no visitor address sent to Google.
- Turning on the Cloudflare proxy (security, step 6) also serves the static files from a server near each visitor.
- Measure at https://pagespeed.web.dev/?url=https://nomadlife.pro
