# Getting at Your Spotify Data

**A plain-language guide to the two ways to pull your own (and your family's) Spotify data out of the app.**

Last verified against Spotify's live documentation and support pages on **September 14, 2026**. Spotify changes these rules fairly often — where a number or a rule matters, this guide says where to re-check it.

---

## The short version

There are two completely separate doors, and you will probably want to walk through both.

| | **The Web API** | **The data export** |
|---|---|---|
| What it gives you | Live, current data: what's playing now, your top artists/tracks, your playlists and saved songs, the last 50 things you played | Your *entire* listening history since the day you opened the account, as files |
| How you get it | Register a free developer "app," then a program calls Spotify on your behalf | Click a button on your account page and wait for an email |
| How far back | Very shallow — roughly the last 50 plays | All of it, every play, since account creation |
| Speed | Instant, any time | Days to weeks, one-time request |
| Good for | Ongoing/automated collection, enrichment (genres, artist info), "what are they listening to right now" | The historical baseline — the thing you actually want for comparing family listening over years |
| Covers other people? | Only if each person logs in and grants permission (max 5 people) | Each person requests their own and sends you the file |

**The honest recommendation for this project:** request the export first, today, because the clock on it is long. Use the API for everything the export doesn't cover.

---

# Part 1 — The Spotify Web API

## What the API is

Spotify runs a public service that lets a program ask questions about music and about a listener's account. You register once to get a set of keys, and from then on any script you write can ask things like "what are this person's top 50 artists over the last six months?"

It is free. There is no paid tier for ordinary use.

## What it can and cannot do

**It can:**

- Look up any song, album, artist, or podcast in Spotify's catalog
- Read a signed-in person's playlists, saved songs, and followed artists
- Read a signed-in person's top artists and top tracks over three time windows (roughly the last 4 weeks, 6 months, and several years)
- Read the last 50 things a signed-in person played
- Control playback (skip, pause, change device) on a Premium account

**It cannot:**

- Give you anyone's full listening history. This is the single most important limitation. There is no endpoint for "every song Marc played in 2023." That only comes from the export in Part 2.
- Give you any data about another person unless that person personally logs in and clicks "Agree"
- Give you play counts, listening minutes, or anything resembling the Wrapped statistics
- Be used by more than 5 people, in practice — see "The 5-person ceiling" below

---

## Step 1 — Sign in to the Developer Dashboard

Go to **https://developer.spotify.com/dashboard** and log in with a normal Spotify account.

Spotify's current documentation states that the account used here needs to be **Spotify Premium**. If you are on a Family plan, your own account qualifies.

You'll be asked to accept the Developer Terms of Service. This is a one-time thing.

## Step 2 — Create an app

"App" is Spotify's word for a set of credentials. You are not building anything, and nobody reviews it. Click **Create app** and fill in four things:

1. **App name** — anything. "Family Listening Analysis" is fine.
2. **App description** — one sentence. Nobody reads it.
3. **Redirect URI** — where Spotify sends a person back to after they approve access. For something running on your own machine, Spotify's own example is `http://127.0.0.1:3000`. Type it exactly; this has to match later, character for character, or sign-in will fail with a confusing error.
4. **Which APIs are you planning to use** — tick **Web API**.

Check the terms box and save.

## Step 3 — Find your two keys

Open the app you just made and go to **Settings**. You'll see:

- **Client ID** — shown right on the page. Think of it as a username for your script.
- **Client Secret** — hidden behind a **View client secret** link. This is the password.

Treat the Client Secret like a password: it does not go in a shared document, a screenshot, or anything that ends up in a public code repository. If it ever leaks, the dashboard lets you rotate it.

## Step 4 — Add your family members

This is the step people miss, and it's the one that matters most for comparing family data.

A brand-new app starts in **development mode**. In development mode it works for you and **up to 5 authenticated Spotify users total**, and each of those users has to be added by hand before they can grant access. Anyone not on the list gets a flat error when they try to log in.

To add someone:

1. Developer Dashboard → your app → **Settings**
2. **User Management**
3. **Add new user**
4. Enter their **name** and the **email address on their Spotify account**

The email must be the one their Spotify account actually uses — not a forwarding address, not the one they check most. If it's wrong, their login fails and the error message won't tell you why.

Once added, each person still has to log in themselves once and approve the permissions. You cannot do this on their behalf, and there is no way around it.

## The 5-person ceiling

Development mode's 5-user cap is a hard limit. The way out of it, **extended quota mode**, is effectively closed to individuals: as of May 15, 2025 Spotify requires applicants to be a registered business with a launched product and **at least 250,000 monthly active users**, and the review takes up to six weeks.

For a family project this is fine — five accounts is usually enough. But plan around it: if you have more than five people, you'll be relying on the exports in Part 2 rather than the API for the extra people.

## What the documentation looks like and where to find things

Everything lives under **https://developer.spotify.com/documentation/web-api**. It's organized in four sections:

| Section | What's in it | When you'd read it |
|---|---|---|
| **Concepts** | How the pieces work — authorization, scopes, rate limits, quota modes | Read this first; it's the part that explains *why* things fail |
| **Tutorials** | Step-by-step walkthroughs, including "Getting started" | The first time you make a call |
| **How-Tos** | Recipes for specific jobs | When you know what you want but not the plumbing |
| **Reference** | Every endpoint, one page each, with a "Try it" console | Constantly, once you're actually building |

**The Reference section is the useful part day to day.** Each endpoint page lists exactly what you send, what comes back, and which permission it needs — and it has a live console where you can log in with your own account and run the call right in the browser. That console is the fastest way to see the actual shape of your data before writing any code.

Direct links worth bookmarking:

- Overview — https://developer.spotify.com/documentation/web-api
- Getting started tutorial — https://developer.spotify.com/documentation/web-api/tutorials/getting-started
- Authorization explained — https://developer.spotify.com/documentation/web-api/concepts/authorization
- List of permission scopes — https://developer.spotify.com/documentation/web-api/concepts/scopes
- Rate limits — https://developer.spotify.com/documentation/web-api/concepts/rate-limits
- Quota modes (the 5-user rule) — https://developer.spotify.com/documentation/web-api/concepts/quota-modes
- Full endpoint reference — https://developer.spotify.com/documentation/web-api/reference

All API requests go to addresses beginning with `https://api.spotify.com/v1/`.

## Signing in: three flavors

Spotify offers three ways for a program to identify itself. You only need to know which is which so you can pick the right documentation page.

| Flow | Does a person have to log in? | Needs the Client Secret? | Can it read personal data? |
|---|---|---|---|
| **Client Credentials** | No | Yes | **No** — catalog only |
| **Authorization Code** | Yes | Yes | Yes |
| **Authorization Code with PKCE** | Yes | No | Yes |

- Use **Client Credentials** for anything about the music itself — looking up an artist, getting album release dates, enriching a track list from the export.
- Use **Authorization Code** for anything about a person — their playlists, their top artists, their recent plays. This is the one you need for the family comparison, and it's the one where each family member logs in once.
- **PKCE** is the same as Authorization Code but designed for apps that can't keep a secret safe (phone apps, browser-only pages). Not needed if your scripts run on your own machine.

## Permissions ("scopes")

When a family member logs in, Spotify shows them a consent screen listing exactly what you're asking for. Each item on that list is a "scope" your program requested. Ask for only what you need — a long list of permissions makes people nervous and doesn't help you.

The ones relevant here:

| Scope | What it unlocks |
|---|---|
| `user-read-recently-played` | Their last 50 plays |
| `user-top-read` | Their top artists and top tracks |
| `playlist-read-private` | Their private playlists |
| `playlist-read-collaborative` | Playlists they share with others |
| `user-library-read` | Their saved/liked songs and albums |
| `user-follow-read` | Artists they follow |
| `user-read-private` | Basic profile — country, account tier |

Full list: https://developer.spotify.com/documentation/web-api/concepts/scopes

## Endpoints most relevant to this project

These are reference-only; each has its own documentation page with a working try-it console.

| What you want | Endpoint | Notes |
|---|---|---|
| Recent plays | `GET /me/player/recently-played` | **Max 50 items.** This is the ceiling — there is no way to page back further into the past |
| Top artists | `GET /me/top/artists` | Three time ranges: short (~4 weeks), medium (~6 months), long (~years) |
| Top tracks | `GET /me/top/tracks` | Same three ranges |
| Saved songs | `GET /me/tracks` | Pages through the whole library |
| Their playlists | `GET /me/playlists` | Then fetch each playlist's tracks |
| Artist details | `GET /artists/{id}` | Genres, popularity — good for enriching export data |
| Search the catalog | `GET /search` | Match export rows back to Spotify IDs |

## Gotchas worth knowing before you start

**Rate limits.** Spotify counts calls in a **rolling 30-second window**. Go over and you get a `429` response, which normally carries a `Retry-After` header telling you how many seconds to wait. Development-mode apps get a noticeably lower ceiling than commercial ones. Spotify does not publish the actual number. Build in a pause between calls and honor `Retry-After` rather than guessing.

**A batch of endpoints was closed off in late 2024.** On November 27, 2024, Spotify restricted several endpoints for apps registered after that date and for existing development-mode apps. Any app you create now **cannot** use:

- Audio Features (tempo, danceability, energy, valence — the "music DNA" fields)
- Audio Analysis
- Recommendations
- Related Artists
- Featured Playlists and Category Playlists
- 30-second preview URLs in multi-item responses
- Spotify-owned editorial and algorithmic playlists

The audio-features loss is the painful one — those fields were the basis of most "compare our taste" analyses. Plan around it: genre tags from the artist endpoint still work, and there are third-party datasets that carry the old audio features if you need them.

**Nothing here is retroactive.** The API only ever describes the present. If you want a history, the export is the only source.

---

# Part 2 — Requesting your historical usage export

## Why this matters more than the API

This is the only way to get your complete listening history. Every song, every podcast episode, with a timestamp, going back to the day the account was created. It's the actual foundation of any family comparison.

It is also slow, so **request it first and let it cook while you set up everything else.**

## The three packages

Spotify's download tool offers three separate things. They're requested independently, arrive separately, and most people only know about the first one.

### 1. Account data

The default, quickest option. A ZIP containing your playlists, your library, your search queries, who you follow, payment and account records, family-plan details, podcast interactions, voice commands, and Spotify's inferences about your interests.

Spotify describes its streaming history as "a list of items (e.g. songs, videos, and podcasts) listened to or watched **in the past year**" — which is why this is *not* the package you want for this project.

### 2. Extended streaming history ← **this is the one you want**

"A list of items (e.g. songs, videos, and podcasts) listened to or watched during the lifetime of your account."

Everything. Every play, with a timestamp, what device, whether you skipped it, how many milliseconds you actually listened. This is the file that makes real analysis possible.

**You have to tick this box separately.** It is not included in the default download. This is the single most common mistake — people request their data, get the standard ZIP, find twelve months of history, and conclude that's all Spotify keeps.

### 3. Technical log information

Device and connection logs — commands, error messages, log strings. Interesting for diagnostics, not for listening analysis. Tick it if you're curious; it costs nothing but a bigger download.

## How to request it

1. Go to **https://www.spotify.com/account/privacy/** and sign in.
2. Scroll to the **Download your data** section.
3. **Tick the box for extended streaming history.** Also tick account data — the playlists and library it contains are genuinely useful, and there's no reason not to take both.
4. Click **Request data** and confirm.
5. **Watch your email.** Spotify's flow sends a confirmation message; if it asks you to confirm, the request doesn't start until you click. When someone's export never shows up, an unclicked confirmation in a spam folder is the usual culprit — worth checking.
6. Wait. Spotify emails a download link when the file is ready.

## How long it takes

The privacy page shows an estimate next to each option, and **that on-screen estimate is the number to trust** — Spotify has adjusted these over time.

As a rough expectation: account data typically comes back in a matter of days, while extended streaming history is the slow one and can run to several weeks. Community reports vary widely. Assume weeks, not days, and don't chase it early.

Two practical notes — both are widely reported by users rather than stated by Spotify, so treat them as caution rather than fact:

- **Assume the download link expires.** Grab the file as soon as the email lands and put it somewhere permanent; don't leave it sitting in your inbox for a month.
- **Assume you can only have one request in flight at a time.** Tick everything you want on the first pass rather than requesting in rounds.

## What arrives

A ZIP of **JSON files**. The extended history comes as a numbered series (one file per chunk of your history), plus a read-me explaining every field.

Read that read-me. It defines the fields precisely, and a few of them are easy to misread — in particular, the field for how long a track played and the fields describing *why* a track started and stopped, which are what let you tell a real listen from a two-second skip. Any honest "top songs" count depends on getting those right.

## Getting your family's data

There is no shortcut here, and no family-plan admin view that exposes it. **Each person must request their own export from their own account.** Being the plan owner gives you no access to anyone else's listening history.

What works in practice:

1. Send each family member the link — **https://www.spotify.com/account/privacy/** — and one instruction: *tick the extended streaming history box, not just the default.*
2. Remind them to click the confirmation email.
3. Tell them up front it may take weeks, so they don't assume it failed and give up.
4. Have them send you the ZIP when it lands. It's usually small enough to email; if not, any shared drive works.

Ask everyone on the same day. The waits overlap, and the slowest person sets your timeline.

## A word on what you're collecting

These files are a fairly intimate record — every podcast someone listened to at 2am, every song they had on repeat during a bad month. Worth being explicit with family about what you're gathering, what you plan to do with it, and where it'll live. Keep the raw files somewhere private, and agree up front on whether the results get shared around or stay with you.

---

# Quick checklist

**Today**

- [ ] Request your own extended streaming history at https://www.spotify.com/account/privacy/
- [ ] Click the confirmation email
- [ ] Send the same instructions to each family member (emphasize: tick the *extended* box)

**While you wait**

- [ ] Create an app at https://developer.spotify.com/dashboard
- [ ] Save the Client ID and Client Secret somewhere safe
- [ ] Add each family member under Settings → User Management (max 5)
- [ ] Have each of them log in once and approve access
- [ ] Try a few calls in the Reference section's try-it console to see the data shape

**When the exports arrive**

- [ ] Download immediately — the links expire
- [ ] Read the field-description read-me before writing any logic
- [ ] Store the raw ZIPs untouched; work from copies

---

## Sources

- Spotify Web API overview — https://developer.spotify.com/documentation/web-api
- Getting started tutorial — https://developer.spotify.com/documentation/web-api/tutorials/getting-started
- Authorization concepts — https://developer.spotify.com/documentation/web-api/concepts/authorization
- Rate limits — https://developer.spotify.com/documentation/web-api/concepts/rate-limits
- Quota modes — https://developer.spotify.com/documentation/web-api/concepts/quota-modes
- Changes to the Web API (Nov 27, 2024) — https://developer.spotify.com/blog/2024-11-27-changes-to-the-web-api
- Get Recently Played Tracks reference — https://developer.spotify.com/documentation/web-api/reference/get-recently-played
- Understanding my data — https://support.spotify.com/us/article/understanding-my-data/
- Data rights and privacy choices — https://support.spotify.com/us/article/data-rights-and-privacy-settings/
