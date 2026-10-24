# FotoHub — Research, Development Strategy and Launch Plan

Companion to `FotoHub_Product_Requirements_and_UI_UX_Documentation.docx` (v1.0).
Desk research, 18 October 2026. Figures marked *(verify)* come from secondary sources or conflict between sources; confirm them before you make decisions on them.

---

## 1. The five findings that should change the plan

1. **Recurring mobile-money billing barely exists in Uganda.** Pesapal's recurring payments work with cards only. MTN MoMo "pre-approval" mandates are uneven across markets and poorly documented for Uganda, and Airtel has no documented equivalent. Don't build the business on automatic monthly debits. Use **prepaid periods** (month, quarter, year) or **per-event credits**, and collect each renewal with a reminder plus a pay link.
2. **Global competitors are cheap and good.** Pixieset paid plans cost about $8–40/month, and its free tier has 3 GB. ShootProof costs about $8–20, and Pic-Time about $7–42. FotoHub can't win on features. It can win on **UGX pricing, mobile-money checkout, WhatsApp-first delivery, galleries that use little mobile data, and local support**.
3. **The people viewing galleries pay for data in small top-ups.** Data costs about UGX 2,000 per GB, and most users buy 500–2,000 UGX bundles. Only about 28% of Ugandans are online, and smartphones are about 34% of devices *(verify)*. A gallery that loads 30 MB before showing anything will fail. Treat a **data budget** as a product requirement (§3.3).
4. **Event photography is moving toward "find my photos" by selfie.** Kwikpic charges about $15–80 per year, 9Pic about $0.02–0.03 per photo, and Samaro delivers galleries over WhatsApp *(verify)*. Keep this out of the MVP, but plan for it as phase 2. It processes biometric data, so it needs legal review first.
5. **FotoHub has legal duties from day one.** Uganda's Data Protection and Privacy Act requires registering with the PDPO, renewing every year, and filing an annual compliance report. If photos are stored outside Uganda, you must keep records of the legal basis for that transfer.

---

## 2. Market and competition

### 2.1 Competitor snapshot *(prices verify — sources conflict)*

| Product | Model | Price (annual billing) | What it's known for |
|---|---|---|---|
| Pixieset | Storage tiers | Free 3 GB; ~$8 / $16 / $24 / $40 | Polished presentation; 15% fee on free-plan sales |
| Pic-Time | Storage tiers | ~$7 (20 GB) / $21 (100 GB) / $42 (unlimited) | Print sales, automated marketing, AI guest search on top tier |
| ShootProof | Photo-count tiers | Free 100 photos; ~$8.33 (1,500) / ~$16.67–19.99 (5,000) | 0% commission, contracts and invoicing |
| Zenfolio | Storage tiers | ~$7 (15 GB) and up; no free plan | Website and galleries in one |
| Photoset (South Africa) | Per order | R9.50 per order, no monthly fee | Local payments (Yoco/Payfast), local print labs |
| Kwikpic / 9Pic / Samaro | Event and face search | Kwikpic ~$15–80/yr; 9Pic ~$0.02–0.03/photo | Guests find their own photos by selfie; WhatsApp delivery |

**Takeaways**
- Photoset shows that **pay-per-use with local payments** can work in an African market. It also suits mobile money better than a subscription does.
- I found no East African competitor focused on gallery delivery. That's an opening, but it may also mean demand is limited. Today's local substitutes are probably Google Drive, WeTransfer, flash drives and WhatsApp media *(hypothesis — confirm in interviews)*.

### 2.2 Ugandan context
- Wedding photography in Kampala typically costs **$100–500**, and full weddings **$500–2,000+** *(verify)*. Assume FotoHub can charge roughly 2–5% of one job per month. Test **UGX 25,000–75,000/month** or **UGX 15,000–40,000 per event** in discovery interviews.
- Wedding vendors gather at Bride & Groom expos. Photographer communities include Kampala Photo Walk / Unpublished Africa and the Uganda Press Photo Award network. These are your channels for early users.
- WhatsApp is the main business channel. It accounts for about 24% of Airtel Uganda's data traffic.

### 2.3 Questions for the discovery interviews (Phase 1)
1. How do you deliver photos today? Ask them to show you their last event.
2. How many photos per event, and how many events per month?
3. What breaks: upload time, data cost, clients losing links, delivery speed?
4. Would you pay monthly or per event? Using what: MoMo, Airtel or card?
5. Would guests use a "find my photos" selfie search, and would couples agree to it?

Interview 8–10 photographers across weddings, graduations and corporate work. Before you write billing code, get **at least 3 to commit to a paid pilot**.

---

## 3. How to build it — recommended changes to the spec's architecture

### 3.1 Split the front end by audience

The spec offers Next.js or Vite React. I recommend a split instead:

| Surface | Recommended | Why |
|---|---|---|
| Customer gallery (`/g/<token>`) | **Server-rendered Django templates**, a small amount of JS for the lightbox, optionally HTMX | Fastest first page on a cheap phone. WhatsApp link previews need the Open Graph tags in the server's HTML. No Node server to run. |
| Photographer dashboard and upload manager | **React + TypeScript (Vite)**, calling DRF | Complex state (upload queue, retries, settings forms) fits React well. It doesn't need SEO. |
| Marketing site (`fotohub.ug`) | Django templates or a static site | Needs to be indexed and fast. Keep it separate from the app. |

This keeps one backend language, removes a Node runtime from production, and makes the most data-sensitive page the lightest.

### 3.2 Uploads

- Use **Uppy** with `@uppy/aws-s3` multipart, uploading straight to object storage. Django provides the signing endpoints: create, sign parts (in batches), complete and abort. Parts must be at least 5 MiB, with at most 10,000 per file.
- **Don't rely on Uppy's Golden Retriever for resuming.** Users report it fails, and re-adding a file restarts it. Track state per file on the server instead: `UploadSession` → part list → idempotent `complete`. If the browser closes, the dashboard can then show "12 of 400 failed — retry those 12". That covers spec requirement FR-12.
- One user has reported that R2 returns 503s and then 500s during long multipart uploads *(verify)*. Set up retries with backoff, cap concurrency at about 3–4 files on mobile connections, and surface the failures in the dashboard.
- Run quota reservation as a single PostgreSQL transaction (`SELECT … FOR UPDATE` on the subscription's usage row) when the upload is created. Release it on abort or expiry. This covers FR-27.

### 3.3 Image pipeline and data budget

- Use **pyvips (libvips)** in Celery workers rather than Pillow. Its `thumbnail()` decodes images at reduced size, and on large images it uses far less memory. One unverified benchmark showed about 18 MB versus 420 MB at 8K. That matters with 24–45 MP originals.
- Variants for each photo:

| Variant | Size | Format | Target weight |
|---|---|---|---|
| Grid thumbnail | 400 px long edge | WebP (AVIF optional later) | 20–40 KB |
| Viewer | 1600 px | WebP | 150–300 KB |
| Web download | 2048 px | JPEG | ~500 KB–1 MB |
| OG/share cover | 1200×630 | JPEG/WebP | **< 300 KB** |
| Original | as uploaded | private | only after an access check |

- **Data budget, as a requirement:** first screen of the gallery under **1 MB**. A visitor who scrolls 100 photos uses under **5 MB**, about UGX 10 of data. Advertise this ("Your clients use less data"). It's a real advantage over galleries built for the US.
- Strip GPS and other EXIF metadata from every public derivative. Keep originals untouched.

### 3.4 Storage, CDN and hosting

- **Object storage: Cloudflare R2.** Egress is free; storage costs about $0.015/GB-month. A gallery business mostly serves bytes, so S3's ~$0.09/GB egress would dominate costs. One example estimate: 5 TB/month served costs about $450 in S3 egress, versus about $23 total on R2 *(verify)*.
- **Cold originals:** Backblaze B2 (~$6–7/TB) can hold originals of archived galleries later. You don't need it for the MVP.
- **CDN:** Cloudflare appears to have a Kampala point of presence (EBB), which would put previews very close to users *(verify on Cloudflare's status page)*.
- **App and database hosting:** Latency to the API matters less than you'd think, because the CDN serves the images. Measured from Nairobi, Cape Town is about 96 ms away and Frankfurt about 153 ms. Fly.io's Johannesburg region has had routing problems. Recommendations:
  - MVP: one EU or South African region with managed PostgreSQL and Redis. Run your own latency tests from MTN and Airtel connections in Kampala before you pick.
  - Keep everything in containers (Docker) so you can move regions later.

### 3.5 Payments

| Option | What it gives you | Notes |
|---|---|---|
| **Aggregator** (pawaPay, Flutterwave, Pesapal, Yo! Uganda, others) | One API for MTN and Airtel, plus cards | Fees roughly 1.5–3% *(verify)*. Pesapal recurring is card-only. Flutterwave supports collecting UGX by MoMo and card. |
| Direct MTN MoMo and Airtel APIs | Lowest fees | Two integrations, separate onboarding, sandbox differences |

Recommendation: use **one aggregator**, accept one-off payments, and model billing as **prepaid access periods and event credits**:
- Subscription state machine: `active → due (D-3 reminder by WhatsApp/SMS/email) → grace (D+0…D+7, uploads frozen, galleries stay live) → lapsed (galleries stay viewable for N days, then archive)`. Never delete photos automatically without several warnings. This matches FR-29.
- Hide the provider behind a `PaymentProvider` interface so you can switch aggregators or go direct later.

### 3.6 Security and privacy additions to §11 of the spec

- Send **`X-Robots-Tag: noindex, nofollow`** on every gallery page and media response. Do **not** block `/g/` in robots.txt: Google then never sees the noindex and may index the bare URL.
- Use gallery tokens of at least 128 bits (`secrets.token_urlsafe(16)` or longer). Rate-limit `/unlock/`. Store a password hash, never the plain text.
- Make the OG preview image a setting. For password-protected galleries, default to the photographer's logo instead of the cover photo.
- **PDPO compliance checklist:** register as a data controller and processor, renew each year, file the annual compliance report, publish a privacy notice, document cross-border transfers (R2 or your hosting region), and name a contact for data requests.
- **Face search (phase 2):** it processes biometric data. Collect explicit consent from guests. Retain face embeddings only while the gallery is live. Get legal review first. Use a vendor API rather than building your own model.

### 3.7 Django project layout

```
fotohub/
  config/            settings/{base,dev,staging,prod}.py, urls, celery.py
  accounts/          User, PhotographerProfile, auth API
  galleries/         Event, Gallery, Album, public views (/g/<token>), policies
  assets/            Photo, UploadSession, storage service, pyvips tasks
  delivery/          DownloadJob, ZIP streaming, signed URLs
  billing/           Plan, Subscription, Credit, PaymentProvider adapters, webhooks
  audit/             AuditLog, GalleryActivity
  frontend/          Vite React dashboard (built assets served by Django/CDN)
```

Main dependencies: Django 5.2 LTS (or the current release), DRF, django-storages (S3 API → R2), Celery + Redis, pyvips, django-ratelimit, Sentry, pytest-django + factory_boy.

---

## 4. Getting it listed on the internet

### 4.1 Foundations (before launch)
- **Name check first.** "FotoHub" is a generic name and is probably already used elsewhere (I haven't searched trademarks). Before you buy domains, check URSB trademarks, domain availability and app or social handles.
- **Domains:** `.ug` costs about $30–77/year depending on the registrar *(verify)*. Also get `.com` if you can. Use the `.ug` domain for local trust.
- **Business email on your own domain.** Many directories reject Gmail addresses.
- **Legal pages:** Terms, Privacy, Acceptable use / abuse reporting, Refunds.
- **Analytics:** a privacy-friendly tool on the marketing site only. Keep analytics off client galleries, or limit them to minimal first-party counts.

### 4.2 Marketing site and SEO
- Pages: Home (one promise: "Deliver event photos by WhatsApp link, pay with MoMo"), Pricing in UGX, a sample gallery demo, features for weddings, graduations and corporate events, FAQ, and a blog.
- Target keywords: *photo delivery Uganda*, *online gallery for photographers Kampala*, *how to send wedding photos to clients*, *Pixieset alternative Uganda*. Write guides in English, and consider Luganda pages later.
- Basics: server-rendered pages, `sitemap.xml` (marketing pages only), Organization/SoftwareApplication JSON-LD, fast Core Web Vitals, and Search Console plus Bing Webmaster Tools.
- Create a **Google Business Profile** for FotoHub as a Kampala business.

### 4.3 The built-in growth loop (the most important channel)
Each delivered gallery is seen by dozens to hundreds of guests, and some of them are next year's clients or photographers.
- Add a small "Delivered with FotoHub" link in the gallery footer. Let higher plans remove it.
- Give the photographer a **"Share to WhatsApp"** button that sends a pre-written message with the link. Test the preview: og:image under 300 KB, at least 300 px wide, and tags near the top of the HTML.
- Referral offer: "Give a photographer 1 free event, get 1 free event."

### 4.4 Directories and launch platforms (in order)
1. **Local first:** photographer WhatsApp groups and Instagram, Kampala Photo Walk / Unpublished Africa, wedding expos (exhibit or sponsor), and photography schools and Canon/Nikon dealers.
2. **Review platforms:** claim Capterra and G2 profiles once you have about 5–10 real users, and ask happy pilot users for reviews.
3. **Product Hunt:** a one-time event with a nofollow link. Launch it once there's a polished demo gallery, not on day one.
4. **Startup directories:** around 20 well-chosen ones (Indie Hackers, BetaList, African tech ecosystem lists), not hundreds of spam lists.
5. **Press and ecosystem:** Ugandan tech outlets (PC Tech Magazine and others), and Kampala startup hubs and accelerators.

---

## 5. Revised roadmap

| Phase | Weeks | Output | Exit gate |
|---|---|---|---|
| 0. Discovery | 1–2 | 8–10 interviews, name/domain check, pricing test | 3 photographers commit to a paid pilot |
| 1. Foundation | 3–4 | Django skeleton, auth, tenant permissions, Event/Gallery models, CI | Cross-tenant permission tests pass |
| 2. Upload pipeline | 5–7 | R2 + Uppy multipart, per-file state, pyvips variants, quota reservation | 1,000-photo event uploads on a Kampala connection with retries working |
| 3. Customer gallery | 8–9 | SSR gallery, lightbox, password, expiry, downloads, ZIP jobs, OG and WhatsApp share | First screen < 1 MB; works on a low-end Android |
| 4. Billing and ops | **Mostly done.** Live since 2026-10-24 at https://139.84.245.0.sslip.io on one Vultr server in Johannesburg (2 vCPU, 4 GB, Ubuntu 26.04 LTS, Docker Compose: Caddy HTTPS, gunicorn, Celery worker and beat, Postgres 17, Redis). Firewall allows 22/80/443 only, and SSH is key-only. Nightly backups go to R2, and a restore was rehearsed on the server. Flutterwave is wired up and tested in test mode (card and UGX mobile money). Still to do: Flutterwave webhook URL, KYC/live keys (Uganda account question), a domain, an SMTP email service, a Sentry DSN, real plan prices. |
| 5. Pilot and launch | 12–14 | 3–5 photographers on real events, marketing site, PDPO registration | Repeat use plus paid renewals |
| 6. Growth | after | Face search (vendor), favorites/proofing, QR codes, print partners | Driven by pilot data |

### Progress (updated 2026-10-24, deployed)

| Phase | Status |
|---|---|
| 1. Foundation | **Done.** Cross-tenant tests pass; CI on SQLite and Postgres. |
| 2. Upload pipeline | **Done.** Exit gate passed 2026-10-21: 1,000 photos (1.9 GB) from Kampala into R2, killed twice mid-run and resumed from the journal, 35 injected drops and 279 retries, all 1,000 processed. 31 files (3%) needed the manual "retry failed" pass. Uppy replaced by an in-house queue. |
| 3. Customer gallery | **Done.** Gate checked 2026-10-24. Emulated low-end Android (6x CPU, slow 4G): first paint 0.86 s, loaded 2.9 s, HTML 12 KB. Real photos: thumbnails average 13 KB, so the first screen is at most about 610 KB. Buy, pay and download worked on the founder's phone. The test fixed a CSRF bug (Referrer-Policy) that would have blocked checkout and password forms. |
| 3+. Photo sales (new) | **Built.** Every gallery sells photos at USD 0.24 or UGX 900 (decided 2026-10-21); cart, "buy all", checkout, order page with original downloads and ZIP. FotoHub keeps the revenue. |
| 4. Billing and ops | **Started.** Subscriptions and a payment-provider seam are done, using a fake provider. Still to do: a real aggregator, plan prices, dashboard billing UI, reminders, Sentry, backups, deployment. |

Phases 1–3 are close to the spec's "six-week" figure. Discovery, billing and the pilot add about 8 weeks for one developer *(estimate)*.

---

## 6. Open decisions for you
1. Pricing model: monthly prepaid, per-event credits, or both. Interviews should decide this.
2. Payment aggregator: get quotes from pawaPay, Flutterwave, Pesapal and one local provider.
3. Hosting region: decide after latency tests from Kampala.
4. Final product name and domain.

---

## Sources
- Competitors: [Pixieset alternatives (Pixpa)](https://www.pixpa.com/blog/best-pixieset-alternatives) · [Pixieset vs ShootProof vs Pic-Time (findme.photo)](https://findme.photo/blog/pixieset-vs-shootproof-vs-pic-time) · [Gallery platform comparison 2026 (12img)](https://www.12img.com/blog/gallery-platform-comparison-2026) · [Pixieset vs Pic-Time vs ShootProof (Kepla)](https://www.getkepla.com/blog/pixieset-vs-pic-time-vs-shootproof) · [Zenfolio alternatives (Aftershoot)](https://aftershoot.com/blog/best-zenfolio-alternatives/) · [Photoset](https://photoset.co.za/) · [Samaro AI](https://samaro.ai/) · [9Pic pricing](https://9pic.ai/pricing/) · [9Pic vs Kwikpic](https://9pic.ai/compare/kwikpic/)
- Uganda market: [DataReportal Digital 2025: Uganda](https://datareportal.com/reports/digital-2025-uganda) · [Monitor: data costs](https://www.monitor.co.ug/uganda/business/prosper/your-data-didn-t-vanish-you-just-watched-a-few-videos-5290974) · [Business Times: average data spend](https://businesstimesug.com/ugandas-11000-shilling-mobile-data-habit/) · [PC Tech: UCC Q3 2025](https://pctechmag.com/2025/12/ucc-report-ugandas-digital-footprint-grows-modestly-in-q3-2025/) · [PC Tech: Airtel data usage](https://pctechmag.com/2025/09/airtel-ugandas-financials-results-reveal-data-is-king-over-voice/) · [GSMA report coverage](https://businesstimesug.com/ugandas-digital-leap-fast-connections-slow-adoption-gsma-report/) · [Progigfinder Kampala photography](https://www.progigfinder.com/services/photography/kampala) · [Kampala Photo Walk](https://luma.com/8cqy7z94)
- Payments: [Pesapal recurring API](https://developer.pesapal.com/how-to-integrate/e-commerce/api-30-json/recurringpayments) · [Pesapal recurring blog](https://www.pesapal.com/blog/understanding-monthly-recurring-payments-how-they-work-and-why-they-matter) · [Flutterwave Uganda mobile money](https://developer.flutterwave.com/docs/uganda) · [MTN MoMo pre-approval forum](https://momodevelopercommunity.mtn.com/momo-api-production-q-a-7/preapproval-renew-and-validitytime-ghana-471) · [pawaPay fees](https://pawapay.io/fees) · [Accepting mobile money in Uganda (mctaba)](https://www.mctaba.com/learn/uganda/accept-mobile-money-payments-website-uganda) · [Kolonell: MoMo recurring billing Uganda](https://kolonell.com/en/blog/recurring-billing-mtn-momo-saas-uganda-2026) · [Kolonell: choosing a provider](https://kolonell.com/en/blog/choosing-payment-provider-uganda-mtn-airtel-guide-2026)
- Legal: [DLA Piper: PDPO and offshore entities](https://privacymatters.dlapiper.com/2025/08/uganda-data-protection-regulator-clarifies-compliance-requirements-for-offshore-entities) · [Afriwise: PDPO offshore](https://www.afriwise.com/blog/ugandas-data-protection-regulator-clarifies-compliance-requirements-for-offshore-entities) · [AF Mpanga: registration and annual report](https://afmpanga.com/compliance-with-registration-and-annual-compliance-report-under-the-data-protection-and-privacy-act-cap-97/) · [Captain Compliance: Uganda law](https://captaincompliance.com/education/ugandas-data-protection-law/)
- Engineering: [Uppy AWS S3 docs](https://uppy.io/docs/aws-s3/) · [Uppy batch presigning](https://community.transloadit.com/t/presigning-urls-in-batches-for-aws-s3-multipart-upload/15774) · [Uppy resume issue](https://community.transloadit.com/t/resume-broken-s3-multipart-upload/16544) · [pyvips](https://pypi.org/project/pyvips/) · [Image library comparison 2026](https://www.pistack.xyz/posts/2026-06-20-image-optimization-libraries-libvips-sharp-imagemagick-pillow/) · [R2 vs S3 vs B2 (infratally)](https://infratally.com/articles/cloudflare-r2-vs-aws-s3-vs-backblaze-b2-2026/) · [R2 pricing explained (mecanik)](https://mecanik.dev/en/posts/cloudflare-r2-pricing-explained-real-costs-vs-s3-and-backblaze/) · [Cloudflare Kampala status](https://www.openstatus.dev/status/cloudflare/kampala-uganda-ebb) · [AWS region latency from Nairobi](https://dev.to/copubah/how-i-used-cloudpinginfo-to-pick-the-best-aws-region-for-my-users-10h3) · [Fly.io JNB routing thread](https://community.fly.io/t/requests-being-routed-via-far-away-edge-cdg-fra-to-jnb/24432)
- Listing and SEO: [WhatsApp link previews (Meta)](https://developers.facebook.com/documentation/business-messaging/whatsapp/link-previews/) · [Google: block indexing with noindex](https://developers.google.com/search/docs/crawling-indexing/block-indexing) · [.ug domain pricing](https://domainoffer.net/tld/ug) · [SaaS directories 2026 (dev.to)](https://dev.to/sergiuchiriac/best-directories-to-submit-your-saas-for-visibility-2026-edition-593g) · [Directory submission lessons (Indie Hackers)](https://www.indiehackers.com/post/what-i-learned-submitting-my-saas-to-15-directories-last-week-2a53f3dfd1)
