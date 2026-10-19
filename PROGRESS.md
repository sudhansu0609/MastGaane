# MastGaane — Progress Log

Newest first. A row is **done** only with the evidence that proves it.

## 2026-10-18 (morning) — the shelves to 15,000: the Hindi rejects back, Bhojpuri lokgeet, the golden age deeper

Asked: "I want the Hindi ones" (the 17 Oct rejects), "lots of folk songs in Bhojpuri, a few in Rajasthani
etc.", "you can add 10000 songs as well, no limit", "more songs of golden era artists".

| Done | Evidence |
|---|---|
| Of the 2,241 rejects of 17 Oct: 1,268 wrong language (Bengali 451, Tamil 225, Telugu 195…), 360 the LLM's "not a song as sung", 296 cut by the 200-song 2010 cap, 202 by genre (indie 150: Lucky Ali, Adnan Sami, Penaz Masani), ~75 remakes / strays | counted from `classify_reason` |
| rules.yaml: `allowed_genres` + indie, sufi, classical; `allowed_languages` + braj, rajasthani, haryanvi, garhwali, kumaoni (classify.py `Language` and the labelling prompt too); `library.target` 15,000, `modern_max` 1,500, 204 artists (caps sum 17,180), 102 playlists | `tests/test_library.py` 10 pass; whole suite 274 |
| Folk and bhajans are timeless (`library.TIMELESS`): a recording's year is the upload's, so they never count against `modern_max` and are never "a remake" (17 Oct: a 2016 chhath geet under Sharda Sinha was cut by the cap) | `test_folk_and_bhajans_are_never_cut_by_the_2010_line` |
| The stray rule ("from 2014, and not Vishal-Shekhar's") only under a name whose own songs are before 2000: a modern composer's singers are theirs | `test_a_modern_composers_singers_are_not_strays`; 19 such Vishal-Shekhar songs had been dropped |
| A source's cached row list is keyed by its cap (`_cached(name, fetch, cap)`): a bigger cap fetches more; albums per artist `max(30, cap // 3)` | the 17 Oct caches (cap 100–250) would have starved Lata's 900 |
| 912 rejects deleted for a second judgement (891 Hindi, 14 Bhojpuri, 5 Urdu, 2 Maithili: the quota, genre, stray and "not a song" ones; language rejects stay) — backup `data/mastgaane.before-library2-2026-10-18.db` | 1,495 rejects left |
| Golden age: Rafi 800, Lata 900, Kishore 800, Asha 600, Mukesh 400, R. D. Burman 450, L-P 400, S-J 350, Naushad / Madan Mohan 220…; 19 more 1940s–60s names (Sudha Malhotra, Zohrabai, Amirbai, Khemchand Prakash, Hansraj Behl, S. N. Tripathi…) and 21 playlists by name (1950s…1970s, "naushad hits", "geeta dutt hits"…) | rules.yaml |
| Bhojpuri lokgeet: 22 singers (Madan Rai, Bhikhari Thakur, Vindhyavasini Devi, Devi, Gopal Rai, Tarabano Faizabadi, Maithili Thakur…) and 18 themed playlists (chaita, kajri, sohar, nirgun, bidesiya, chhath, purbi…); Rajasthani 9 names (Mame Khan, Ila Arun, Seema Mishra, Allah Jilai Bai, Langa / Manganiyar) + 4 playlists; Haryanvi, Garhwali, Kumaoni a few | rules.yaml |
| The build runs: `mastgaane library` → `data/library.2.log` (the 12B model, ~0.8 s a song: hours) | pending — the counts go here when it ends |

## 2026-10-18 — Dexter's ear over the music: short windows, not a pause

After "can you play MastGaane for me?" (00:34) nothing said over the music reached Dexter or MastGaane
for six minutes. Dexter's log: every capture "Ignored N s over MastGaane's music (no 'Dexter' in it)", or
a loop ("I am happy, I am happy…", "Chhaya Geetmala" ×28), or Whisper's prompt written back ("Hawa Mahal,
Chhaya Binaca Geetmala, preview." — sent to the chat as a turn); "Dexter" came out as "Extra". From 00:55
(dexter-b1's 7 s cap live) four tries were "Ignored someone else (owner 0.30–0.38)" before "Bardi stop."
got through at 01:02. All of it in Dexter's `voice_pipeline` (MastGaane's code is unchanged).

| What | Evidence |
|---|---|
| **Why**: over continuous music Silero never hears a pause, so a capture runs to its cap (7 s + 1.2 s pre-roll; 20 s before 00:45) and Whisper — beam 5, the ~160-token vocabulary prompt — writes the song; a 1–2 s command is a sliver of it, often split across two captures | room simulation of that path (captures on a random grid, the real routing incl. `dexter_part` / `music_command`): **365/640** commands heard, median **4.0 s** after the last word, p90 6.8 s |
| **The ear** (`voice_pipeline._run_ear`): while MastGaane plays, no VAD captures; every 1.25 s the last 3.5 s go through the same GPU Whisper — one greedy pass, ≤ 40 tokens, **no prompt** (`stt_engine.screen_text`, 0.17 s a window); a name anywhere → a quick command at once (Bharti: stop / next / agla / pause / mute / what's playing → `/api/voice/heard`; Dexter: stop / pause the music…), otherwise the music dips and the rest is recorded (≤ 5 s; done when a window adds nothing) and written down without the prompt | same windows: **546/640 (85 %)**, median **0.7 s**, p90 1.7 s. End to end through the real `VoiceSession` + Silero + turbo, the song really dipping (2 voices × near/far × 0/+4 dB × 2 songs × 7 sentences): **86/112**, median **0.8 s** after the last word with the compute, p90 1.3 s, max 2.8 s (a question, written down once nothing more came); music alone, 6 songs × 60 s: no action, no dip; one `feed()` at most 0.66 s |
| No prompt — primed it echoes | primed with "Dexter. Bharti." Whisper wrote "Bharti." into **791 of 870** windows of plain music (21 songs); unprimed, a name into **none** |
| A second, primed listen only when a window has a command's or a Dexter-like word but no name ("Happy next song", "Hearty Paws", "Next step." = "Dexter") — kept only if it holds "Dexter", or "Bharti" + a quick command | 489 → 546 heard; over the music 2 second listens in 870 windows, 0 acted; primed never wrote "Dexter" over music or the host (0 of 1 181) |
| How Whisper writes the names unprimed over a song (the misses, read one by one) | "Bharti" anywhere (Bhaarati, BHAARTHI, Bártí); Marty / Narti / Harti / Ghaarti / Bardi at a clause start; Party / Hearty / Cardi / Dharati only with a command after ("Party, stop."); "Dexter": Dixter, "Extra,"; "Paws" after the name = pause. Not: party alone, dharti, aarti, Bharat, bhakti |
| The host: her links say "Vividh Bharati" ("Vivid Bharti.") | excluded; while she talks (phase `link` + 4.5 s) only a quick command counts, never a lone name, and no second listen ("अगला गीत…"); 40 links, 311 windows: 0 actions |
| A grey voice over the music: owner 0.30–0.38 at the 0.40 bar with Only-me refused "Dexter, …" by name (the four tries at 00:55–01:00) — now the name decides for any rejected print over the music | `test_a_grey_voice_over_the_music_is_heard_by_name` |
| The log says what it heard: "Ignored … over MastGaane's music: '…'"; the ear logs each name it acts on, and every 100 windows the last one, its time and the hop | `test_ignored_over_the_music_says_what_it_heard` |

Tests: Dexter `test_voice_ear.py` (57, the live sentences among them) + music / pipeline / tools_music →
**284 passed**; Dexter's whole backend 2232 passed, 13 failed — the known ones (router, browser, autostart,
ports helper, screen-text routing, two voice-gate tests that follow `.env`), none in this code. MastGaane
270 passed (unchanged). Dexter restarted 01:33 (healthy, pid 21600). MastGaane was stopped by the owner
("Bardi stop.", 01:02) and left stopped, so the ear is **not yet tried live over the music**; when it
plays, Dexter's log shows a line every 100 windows (~2 min) with what the music sounded like.

Known: a lone "Bharti" over a loud song is the weakest (39/64) — Whisper writes it "Party." / "Hearty." /
"Bhabhi", words a song has, so they are never taken alone: say it with the command. "Bharti, pause"
43/64 ("Boss", "Pond"). The misses are mostly the far-field voice as loud as the music.
Next lever: take the song out of the mic before Whisper hears it — MastGaane knows the file and
`listened_s`; a delay-matched subtraction (or an echo canceller fed the same PCM) would leave the voice.

## 2026-10-18 (past midnight) — "Bharti, stop" / "agla gaana" must always work

Listener: "I asked Bharti to stop and agla gaana while it was playing and it did not work — make sure
Bharti adheres to my commands strictly." Dexter's log: "Bharti, play happy song" and "Bharti Jagla Song"
(→ next) worked at 23:53; after that only "Ignored someone else (owner 0.31…)" and "Ignored 15–17 s over
MastGaane's music (no 'Dexter' in it)". Three ways the owner's words never reached MastGaane, all in
Dexter's voice pipeline (MastGaane's ear since last night):

| Cause | Fix (dexter/backend/voice_pipeline.py) | Evidence |
|---|---|---|
| The voice match: on the new Yeti mic the owner scores 0.30–0.36 under the 0.40 bar; with the music paused or quiet the capture was dropped before it was transcribed | a rejected capture is still transcribed while MastGaane runs (or waits for a command), handed over when it is for MastGaane, else ignored as before — a music remote is no secret | `test_bharti_is_heard_whoever_says_it` |
| Over continuous music a capture runs 15–17 s: "Bharti, stop" came after the lyrics, and only a capture *starting* with the name was handed over | `mastgaane_part`: a "Bharti" at any clause start, with at most 6 words after it (a lyric runs on: "…, Marty, I don't like the way you look at me tonight" would have dropped a song for ever) | `test_bharti_is_found_where_a_sentence_starts`, `test_bharti_after_the_lyrics_in_a_long_capture` |
| Whisper turned a whole capture into a loop ("No, no, no…") and it was dropped with the command in it | a dropped capture's raw text is still searched for Bharti | `test_bharti_inside_a_dropped_music_loop_is_still_heard` |

And in MastGaane: a sentence handed over with the name must sound like a command (`confident`), quietly
ignored otherwise. Live through `/api/voice/heard`: "Bharti, stop" → Stopped, "Bharti agla gaana" → Next.
`LIBRARY_LLM_MODEL` (12B) added: Dexter's session set `LLM_MODEL` to the 4B model at the owner's request,
and the library labels must not follow it. Dexter was restarted by his own session (it was editing the
same files).

## 2026-10-17 (late night) — a radio's shelves: 5,000 songs, mostly before 2010; it learns your hours

Listener: "is it like a good FM channel, a jockey who understands my taste?" — partly: 125 songs (44 from
2010 on), one played three times an evening in two uploads, 56 plays ever to learn from, a fixed hour lean.
Asked for: no repeats across uploads, learning what they like at each hour, and "maybe 5000 songs, most
before the 2010s and maybe 120–200 after".

| What | Evidence |
|---|---|
| One song, many uploads: `lexicon.work_key` (consonant shape of the short title without n: me/mein, hai/hain) + a singer in common; the jockey skips another upload of any of the last 40 songs and keeps one upload per song in the pool | on the collection it found exactly the real pairs (Hum Tere Shahar…, Darmiyaan / Unplugged, Raabta, Ilahi, Alfaaz, Main Yahaan Hoon); "Tum Ho" ≠ "Tum Hi Ho"; tests `test_another_upload_of_a_song_just_played_is_a_repeat`, `test_one_upload_per_song_in_the_pool` |
| The hour learns: `jockey.hour_taste` — per part of the day, the moods and genres finished (+1), liked (+3), skipped (−1), disliked (−4) — weighs each pick ×0.4…×2.7 | `test_the_hour_learns_what_you_like_then` (10 sad songs finished at night: the next sad one ×2.7 then, not in the morning) |
| The collection: `library.py`, `mastgaane library`; rules.yaml `library` (88 singers / composers with a number each, 10 playlist searches, target 5000, `modern_max` 200); status `library`, played like kept songs; your own songs keep 3 picks in 10 (`FAVOURITES_SHARE`) | `tests/test_library.py` (filters, labels, the cap, a second run asks the LLM nothing); a live 30-song trial: 29 Rafi songs 1955–1983 in Hindi, Lata's Marathi dropped, one glued-on "…NahinRevival" caught and the filter fixed |
| Labelling model: the 12B local model (gemma-4-12b), compact `n|language|genre|mood|year|keep` lines, ~0.8 s a song; never a paid API | the same 24 songs: the 4B model called every Bengali song Hindi, the 12B got all six; its years were right on the 2010 line for all 24 (some a decade off: Pyaasa's song came back 1970) — JSON-schema replies were cut off (the schema took 3,500 of the resident model's 4,096 tokens) |
| Remakes kept out of the 200 after-2010 places: an old composer's albums from 2010 on are skipped, and a song from 2010 on must credit the singer / composer whose page it came from | live, first 1,000: 27 "after 2010" songs were remakes and strays under R.D. Burman, Laxmikant–Pyarelal, Kalyanji–Anandji ("Dil Mein Chhupa Loonga" 2016, "King Of Bihar" 2026) — deleted and judged again; `test_an_old_composers_new_albums_are_skipped`. Telling the LLM to drop "remakes" was tried and taken back: it dropped originals ("Mera Jeevan Kora Kagaz", "Rimjhim Gire Sawan"; Kishore 10 of 30 kept → 26 without it) |
| …and a song from 2010 on under a name whose songs centre before 2000 is a remake (`OLD_ERA`; the source's median year) — remakes credit the original composer, so the credit rule let them in | at 2,000 songs: 41 such dropped ("Ilu Ilu" 2018 under Kavita Krishnamurthy, "Bhankas" 2020 under Bappi Lahiri, "Har Kisi Ko" 2013 under Kalyanji–Anandji); the after-2010 songs that stay are Shreya Ghoshal's (her median 2014), Sunidhi Chauhan's (2010), Kailash Kher's, Lucky Ali's; `test_a_song_from_2010_on_under_an_old_name_is_a_remake` |
| Voice wish matching with ~6,000 names | `correct_wish` 0.2–0.7 s with 6,200 names ("leharman chalisa" → Hanuman Chalisa still) |

| **Built: 5,000 songs** (yours 125), 200 from 2010 on; 1950s 128 · 60s 342 · 70s 444 · 80s 812 · 90s 1,195 · 2000s 792 · year unknown 1,066; film 3,612 · ghazal 566 · devotional 536 · folk 286; hindi 4,708 · bhojpuri 151 · urdu 104 · maithili 29 | `mastgaane library --stats`; ~2,980 songs added and ~1,500 dropped (Bengali, Marathi, Punjabi, Tamil, remakes, junk) over ~75 min of the 12B model |
| A first pass stopped at 4,127: duets are shared by Lata, Rafi, Kishore and the composers (one song, once), and six names found nothing — YouTube Music's first "Ghulam Ali" is Ustad Bade Ghulam Ali Khan, "Khayyam" has three pages, the first without songs. Now: the exact-name pages in turn, then a song search crediting the name; 22 more names and 20 playlist themes (rain, Holi, Chhath, Shiv / Krishna bhajans, Lata–Rafi duets…) | Ghulam Ali 0 → 60 songs found, Khayyam 0 → 83; `test_a_singer_without_a_page_is_found_by_their_songs` |
| With 5,000 titles, misheard wishes: "leharman chalisa" found the singer "Caralisa" — a window that leaves a word out now needs the strict bar; title shapes cached and impossible lengths skipped | "leharman chalisa" → Hanuman Chalisa again; 1–2 s → 0.04–0.23 s a wish |
| Playlist video rows had "939K views", "Jan 21, 2022", "90's Gaane" as the artist (the host would read it out) | 54 artist fields cleaned; `test_a_playlists_view_count_is_not_a_singer` |

Known: 2,777 of the 5,000 are labelled "romantic" (the LLM's default) — the hour learning corrects the
lean as you listen; 1,066 have no year, so "60s songs" misses them. While it built, the 12B model pushed
Dexter's fast model off the GPU; his steward put it back afterwards.

## 2026-10-17 (night) — Mute; close and Dexter starts it again; Dexter is the ear

Listener: "make sure I can mute MastGaane … close it, and Dexter can play it if I ask him", then "let
MastGaane use the same voice engine as Dexter — it is not good at voice commands".

| What | Evidence |
|---|---|
| **Mute** (`Player.mute`, `POST /api/player/{mute,unmute}`, tray *Mute*, "Bharti, mute / awaaz band karo / unmute / awaaz kholo"): the show goes on silently; a dip, Dexter talking or the next song stay silent; play / resume unmute | live: ffplay's Windows session volume 1.0 → 0.0 → 1.0 through the API, and through `/api/voice/heard` ("Bharti." → 0.2 dip, "mute karo" → 0.0, "Bharti, unmute" → 1.0); `test_mute_keeps_the_show_going_silently`, `test_mute_and_unmute_by_voice` |
| Close, then Dexter plays it | Dexter's own `stop_app("mastgaane")` then `mastgaane_play("ghazals")`, live: port and every MastGaane process gone; relaunched in 7.5 s, Ghulam Ali playing 36 s later (the host's link first). His "close mastgaane" → `stop_app` (test_router) |
| Dexter: `mastgaane_mute` ("mute the music", "gaana mute karo", "unmute mastgaane"); a bare "mute" stays the PC's (`set_volume`), whose unmute also unmutes MastGaane | Dexter `test_tools_music` (fast path, tool, `unmute_if_muted`); semantic tier checked: "can you mute the music" → mastgaane_mute, "mute" / "mute the sound" → set_volume |
| **Dexter is the ear.** MastGaane's own mic is off by default (`VOICE_CONTROL=false`); `/api/voice/heard` works without it; after "Bharti" alone MastGaane chimes, dips and answers `listening: true`, and Dexter sends the next sentence ("agla gaana", "stop") with no name; MastGaane closed, "Bharti, ghazal sunao" makes Dexter start it | why: on the new **Yeti** mic (speakers heard at −12 dBFS, the Brio −37) MastGaane's CPU Whisper opened **26 listening windows in 47 min, none a call**, then one every ~8 s on Hawa Mahal's dialogue; Dexter on the same music handed over only real calls ("FM, mute.", "Bhaarati, Leharman Chalisa.", "Hey Bharti, play some …"). Tests: `test_dexter_is_the_ear`, `test_heard_route_works_without_our_own_mic`, Dexter `test_after_the_name_alone_the_next_sentence_is_mastgaanes`, `test_after_bharti_alone_the_next_sentence_goes_to_mastgaane`, `test_bharti_starts_a_closed_mastgaane_for_a_wish` |
| Own mic (when on): over the music a name with no command is always heard again unprimed; "part" no longer counts as the name | 62 saved Yeti echo clips + 17 Hawa Mahal ones re-heard unprimed (base and small): a name in 0 once "part" is out (it was the only 2). A "Whisper is sure" shortcut was tried and dropped: the drama gave "Bharti." at no-speech 0.01–0.10, the owner's real call 0.04 |

Pending: Dexter must be **restarted** for his part (the follow-up after "Bharti", `mastgaane_mute`,
starting a closed MastGaane) — another session was editing Dexter at the time, so it was not restarted
here. Until then "Bharti, next song" in one breath works (his running code already hands those over).

## 2026-10-17 (evening) — "Bharti is not able to catch my voice": mishearing protection

Listener, after trying it with their own voice: it should have spell-correction / mishearing
protection like Dexter's. `data/voice_debug.log` (16:48–17:09) showed why it failed:
- ~25 "Bharti." heard over the music and **dropped by the echo guard** (a lone primed name with
  no-speech ≥ 0.3 was taken for Whisper writing its prompt back; edge-tts voices had stayed ≤ 0.22,
  a real voice across the room did not);
- "Marthi." and "Bhaarati, Leharman, Chalisa." (= "Bharti, Hanuman Chalisa") not understood;
- one capture ended 2 s after the chime with only "You" (the listener had not started yet);
- at 15:04 a video's "How do you feel about Bharti's studio?" played Coke Studio ("studio").

Done:
- **`lexicon.py`** (after Dexter's `backend/lexicon.py`): `skeleton` / `loose_skeleton` / `score`;
  `HEARD` — "Bharti" by how it sounds (b/bh/m/sm/v + "ar" + t/th/d + i/ee/y: Marthi, Marty, Bhaarati,
  Smartie, Barty…; not party, Bharat, bhakti, aarti, the lyric "marte"); `correct_wish` — a wish
  matched to titles (honorific-free too), singers and programmes by consonant shape, WISH_T 0.76,
  0.86 for short names / partial windows, ≤ 5 words, no stop-word edges; a rare title word heard right
  ("chalisa") names its song. Checked on a day of the room's chatter as Whisper wrote it (1 229
  chunks): 0 false names (33 before the limits).
- **Voice control**: misheard names count at a clause start/end only (not inside a lyric); a lone name
  Whisper was unsure of gets a **second, unprimed listen** (`_confirmed`, loose `sounds_like`) instead
  of being dropped; a name said twice is not a wish (it fuzzily became the genre "bhakti"); the
  spelling match for short names is 0.86 (at 0.8 "bhakti", "Bharat", "aarti" were "Bharti"; `NOT_NAMES`);
  possessives are never a call; generic title words ("studio", "remix", `lexicon.COMMON`) are no wish;
  "Bharti ji" = the name; `PROMPT_REPLY_S` 2 → 3 s; VOICE_DEBUG logs nsp + every decision and keeps
  the last 120 clips with a name or command in `data/voice_debug/`.
- **Dexter** (uncommitted): `_MASTGAANE_WAKE` takes the same misheard forms, so "Marthi, …" is handed over.
- **Echo threshold from data**: on 8 of the owner's songs (90 s each, −38 dBFS) the primed fast model
  wrote names-only in 351 of ~470 windows; the 205 lone-name echoes all had no-speech ≥ 0.47, and an
  unprimed second listen (base or small) found a name in 0 of 351. Names said as loud as the music:
  0.26–0.58, mostly < 0.45 → `ECHO_NSP` 0.3 → 0.44. The Silero gate on lone names was dropped (it
  heard the singers in 75 of 205 echoes).

**Evidence.** `pytest` → **233 passed** (44 new in `test_lexicon.py`, voice tests for misheard names,
possessives, a name said twice, the second listen). Room simulation, a lone name over two songs
(5 edge-tts voices × near / far-field reverb): voice 3 dB above the music **19/20 caught** (before:
12/20 with the first cut of this fix, ~10/20 with last night's rule); as loud as the music **9/20**
(before: 4/20). Music alone, 4 songs × 60 s: one listening window opened by a lyric that sounds like
"Bharti" (the next window: "Bhartiya"; Whisper sure of it at 0.26, so the old rule opened it too), no
action. Dexter: music/hand-over tests 55 passed; full suite 13 failures, none in the code touched
(router, browser, autostart, ports helper, voice gate). MastGaane restarted 18:02 (was paused);
it now listens on the **Yeti** (newly plugged in; the Brio reads −91 dB). Dexter had restarted itself
at 17:39, after the hand-over edit, so it already runs it. **Still not tried by the listener's voice.**

## 2026-10-17 (night) — "Bharti" … *ding* … "agla gaana": the voice control rebuilt

Listener, after "Bharti did not respond" twice: the name first, then wait for the command; fast;
Dexter must ignore anything starting with Bharti, Radio or FM; "think through all the cases".
Their debug log showed why it failed: the Brio hears the speakers non-stop (−38…−44 dBFS) and the
fast model wrote "Bharti" as "Marty" / "Smartie" / "Mark" over the music.

- **Flow** (`voice_control.py`): a name alone → chime, dip, a recording (`_Capture`) of what comes
  next, ended by Silero VAD (faster-whisper's) *and* +9 dB over the dipped music; a name + command in
  one breath → done at once. Music is read in 3 s windows every 1.5 s **from the first second** of a
  sound (the old segmenter threw away the first 8 s), the name may come anywhere in a window.
- **Never acting on the songs or the host** — each caught in a simulated room: prompt echoes
  ("Bharti, Radio." → now only names-only + primed + no-speech ≥ 0.3), Whisper loops ("Bhajan. Bhajan…"
  — music words removed from every prompt; `_babble`), lyrics holding a name (`confident`: a command
  word within the first words, or a play verb / a few known words), a cut-off "gha" matching Ghulam Ali
  (whole-word singer/title matches), "chazil" → "chal" → "calm" (whole command words only; fuzzy wishes
  only to 5+ letter genres), "fazil" → "film"; host guard (her link + 4.5 s: stop/pause/next only, no
  listening window); her lines and the LLM prompt no longer say रेडियो / भारती / FM.
- **Speed**: one greedy Whisper pass, ≤ 40 tokens, no retries (music had taken ~3 s per window);
  quick commands act on the fast model; `small` only for wishes and for the recorded command.
- **Dexter** (uncommitted): `for_mastgaane()` — a sentence starting with Bharti/Radio/FM is not
  Dexter's; `tools_music.hand_over()` → `POST /api/voice/heard`; MastGaane does a command heard twice
  once (`_do` dedup 5 s). Dexter restarted 03:12 (`Dexter.vbs --tray`), healthy, open mode.

**Evidence.** `pytest` → **171 passed**; Dexter 487 passed (3 known failures: 2 pre-date this work,
1 fails whenever MastGaane runs). Room simulation (`room_sim.py`, real songs at the Brio's level,
edge-tts voices, the music really dipping when ducked), voice +8 dB over the music: 15/16 commands
done 0.2–0.6 s after the last word (one wish misheard: "fazil" → film, fixed after); voice +3 dB
(the live log's margin): every "Bharti" command on both songs (10/10), "Radio"/"FM" 0/4; music alone
(2 × 40 s per run, 6 runs) and ordinary talk: no action, no dip. Live: Dexter → MastGaane hand-over
of "Bharti, what is playing?" answered. **Not yet tried by the listener's own voice** (MastGaane left
stopped — they had paused it; VOICE_DEBUG on for the morning).

## 2026-10-17 — "Bharti, agla gaana": voice commands without Dexter; the day's rhythm; Dexter over the music

Listener: control MastGaane by voice without Dexter, with its own wake word (not "MastGaane" —
"radio / bharti / fm"); the music should dip while they speak and "stop" should stop at once;
songs by time of day (morning bhakti, night ghazals, the programmes); Hanuman Chalisa when it
first starts in the morning; and Dexter ignored them while MastGaane or its host was playing.

- `voice_control.py` (in `serve`, `VOICE_CONTROL`): ffmpeg DirectShow mic (Brio) → loudness
  segmenter → Whisper on the CPU (small for commands, base for checks, 2 threads) → `split_wake`
  (Bharti / Radio, fuzzy: "Bharati", "Ghaarti"; short names need a near-equal length, so "Hari" and
  "party" are not "Bharti") → `parse` (Hinglish words canonicalised: "aglah gana chalaw") → player.
  "Jagjeet Singh" → the collection's Jagjit Singh. Music on the speakers never ends a sentence, so
  it is read in 4 s windows every 2 s. Tray: *Speak a command*, voice on/off, notifications;
  `GET /api/voice`. Stop / pause / next work even while the host talks.
- **Two live bugs, fixed:** (1) dipping on every loud onset — on these speakers that was every song
  and every intro, and the dip stuck (threads racing, and Windows remembers ffplay's session volume):
  now the music dips only when the first ~1.2 s of a sentence, transcribed *without* a prompt, opens
  with a name (ours or "Dexter"), and `duck.Keeper` holds the current ffplay at its level every second.
  (2) Whisper fed music echoes its prompt: a primed "Bharti, Radio." armed the listener and a lyric
  ("The best night of the day") changed the song. Now every sentence must pass the unprimed name check
  before the primed transcription, a bare name arms nothing, and the prompt holds only the wake words.
- Player: `duck(level, seconds)` + `POST /api/player/duck`; skip / like / dislike debounced 2.5 s
  (one sentence heard by Dexter and by MastGaane = one skip).
- Time of day: `TIME_SHARE = 0.65` of the usual mix suits the hour (morning devotional/peaceful,
  afternoon happy/energetic/nostalgic, evening romantic, night sad/peaceful); a discovery turn stays
  one. `rules.yaml` allows `devotional`; `time_seeds` + `discover --time` + nightly `top_up_times()`.
  `MORNING_OPENER` = Hanuman Chalisa (Hariharan, song 408): first pick of a morning session, once a
  day (a skip counts), with its own Hindi greeting.
- Dexter (uncommitted, its repo): a rejected voice print while MastGaane plays is transcribed and kept
  only when it opens with "Dexter" (even with Only-me on); "Dexter, stop" with Dexter quiet stops the
  music; the music dips while Dexter talks and through its follow-up window. Registry ram 1536 MiB.

**Evidence.** MastGaane `pytest` → **150 passed**. Dexter: `test_voice_music.py` 6 new; voice +
music + router 403 passed (2 failing studio/ladder tests pre-date this). Its log showed the cause:
"Ignored someone else (owner 0.05–0.33)" over the music, dozens a night. Live, spoken commands
(edge-tts voices through the real pipeline): 10/12 understood ~1–1.5 s after speaking, chat ignored;
dip check: 10/11 commands dip, 0 false dips on 6 songs. Over a real song mixed in: unreliable (1/6) —
"Dexter, …" is the surer way on speakers. After the fixes: a minute of real playback at volume 1.0 and
no command from the music. Morning now 29 suitable songs, afternoon 45, evening 92, night 82.
**Not yet tried by the listener's own voice; Dexter must be restarted to load its part.**

## 2026-10-17 — The host speaks with a native Hindi voice (Gemini TTS, free tier)

Listener wants "a good emotional Indian girl voice"; Swara was monotonous, Aarohi (mr-IN) not it;
no billing.

- `gemini_tts.py`: Gemini 3.8 TTS (Interactions API), feeling given as `speech_metadata.style`
  per `jockey.emotion_of`. Voice `GEMINI_TTS_VOICE=hi-in-podcaster-9` from Google's native Hindi
  library (57 female hi-IN voices; `GET /v1beta/voices?language_code=hi-IN`). Designed voices also
  supported (`mastgaane host-voice --design`).
- Free tier is **3/minute and 10/day per model**: requests are paced per model, a spent model rests
  until Google's "retry in 5h32m48s", and the 3.8 Flash and Flash-Lite take turns. 3.1/2.5 TTS models
  refuse the hi-in voices and speech_metadata. `JOCKEY_TALK_EVERY=2` (an RJ does not talk before
  every song; programmes / new finds / caught songs always) and `JOCKEY_FALLBACK=quiet` (no
  Microsoft stand-in once both are spent).
- Config `env_ignore_empty=True`: Dexter's `.env` has an empty `GEMINI_API_KEY=`, which the server
  Dexter launched inherited and which blanked our key.

**Evidence.** `uv run pytest -q` → **88 passed** (+10 `test_gemini_tts.py`, +1 `test_config.py`).
Live: Whisper clarity on a sad intro with English-letter names — designed voice 77%, hi-in-podcaster-1
90%, hi-in-podcaster-9 86% (Flash-Lite), Swara ~80%; Gemini pace 126–189 wpm across feelings.
Restarted through Dexter: the first link ("किसी ख़ास की याद आ रही है?…", tender) is a Gemini clip
(cache key match). Flash's day was spent on testing, so Flash-Lite spoke it.

## 2026-10-16 — A host with feelings, a tray icon, Dexter's clean stop

Listener: the host's voice is "too monotonous", wants an icon in the tray to stop it, and Dexter
must be able to reach the app.

- **Voice** (`jockey.py`): `emotion_of(item)` turns the song's mood into the host's feeling
  (sad → wistful, romantic → tender, energetic → excited, peaceful/devotional → calm, old programme
  → nostalgic, a just-caught song → excited). Templates are now per feeling, with interjections,
  "!" and "…" breaths; the LLM prompt gets the feeling and asks for the same. `_delivery()` speaks
  each sentence in its own edge-tts rate / pitch / volume (lift on "!", rise on "?", slowing into
  "…", softer after 23:00, smaller Hz steps for male voices); `_stitch()` (ffmpeg) cuts each clip's
  ~1.2 s of edge silence and puts chosen pauses between. Cache key includes the delivery.
- **Tray** (`tray.py`, pystray + Pillow): `mastgaane serve` shows an amber note icon (`TRAY=false`
  or `--no-tray` to hide). Tooltip/menu = what is playing; left click play/pause; Next, Like,
  Never again, Stop music, Open MastGaane, Quit MastGaane. `serve` now runs `uvicorn.Server` so
  Quit can end it cleanly.
- **Dexter**: `POST /api/shutdown` (loopback only) stops ffplay and the server; Dexter's registry
  stop for mastgaane is now `health_route` → that route, falling back to the process tree (a hard
  kill left the ledger entry and a ghost tray icon). Dexter router: "pause/resume/skip mastgaane",
  "turn off mastgaane", "gaana band karo", "agla gaana" → `media_control` (handed to MastGaane).

**Evidence.** `uv run pytest -q` → **77 passed** (+3 voice, +4 `tests/test_tray.py`). Voice,
rendered live with Swara: median pitch of a sad link 260 Hz vs an excited one 308 Hz (the old
flat links: 276 vs 281 Hz); Whisper transcribes the stitched clips word for word; links ~10 s
(were 11–14 s before trimming). Live on a second `serve` (port 8795, scratch ledger): the
process owns a `…SystemTrayIcon` window; `/jockey/next` returned a 5.2 s 24 kHz mp3 link;
Dexter's `tools_music` found it, then `_stop_health_route` → ok, ledger emptied, process gone.
Dexter: router music tests 22 passed (+10), registry test updated; `test_a_busy_preferred_port…`
fails only while a real MastGaane runs (its probe finds the live process). **Not yet heard by
the listener**; the running instance still has the old code until restarted.

## 2026-10-16 — 🎤 Listen: Shazam inside MastGaane

Listener asked for a Shazam-like service: play a song, MastGaane names it, saves it to the
collection and plays it.

- **Shazam was never running.** shazamio imports `audioop`, which Python 3.13 removed; the
  ImportError was swallowed, so resolver tier 2 silently returned nothing on every reel since
  2026-08-23. Added `audioop-lts`. `resolver/fingerprint.py` now also exposes the full track record.
- `mastgaane/listen.py`: record (ffmpeg DirectShow, every real mic at once, loudest wins — the
  Windows default mic here is the silent *Oculus Virtual Audio Device*, Realtek reads −76 dB,
  the Brio 100 webcam mic is the live one; Bluetooth `Headset (…)` mics skipped), PC sound
  (`soundcard` WASAPI loopback), or any uploaded audio/video (ffmpeg → wav, tried in 12 s
  windows). Saves as approved + `Catch` row, YouTube id (anonymous ytmusicapi), Apple id /
  year / cover from Shazam; classifies in the background.
- `jockey.pick()`: a catch not played since it was caught comes first when there is no wish,
  with its own Hindi link. `Player.play_item()` puts it on now / next.
- Web: `/listen` tab (phone-first buttons when opened off the PC; iPhone camera capture;
  Shortcut recipe), `POST /api/listen`, `/api/listen/file`, `/api/listen/save`,
  `/api/player/song`. CLI: `mastgaane listen [--pc | -f file | --mics]`.
- Header nav now wraps on phones (it overflowed to 709 px at 390 px width on every page).

**Evidence.** `uv run pytest -q` → **70 passed** (+9 `tests/test_listen.py`). Live, scratch
`DATA_DIR`: a song played on the PC via ffplay → `mastgaane listen --pc` → “That's Arz Kiya Hai
Coke Studio Bharat by Anuv Jain. Saved…”, 9.9 s end to end, YouTube id `-BJt4fCAtZE`, Apple id
`1831237608`, year 2025. Through uvicorn: `POST /api/listen` (pc) and a 10 s `.mov` upload both
named it. Mic path live: both mics record in parallel (Brio −50…−87 dBFS in a quiet room) and a
silent room returns a miss, not a guess — **not yet tested with music playing into the mic.**

## 2026-10-15 — PC player for Dexter and Alexa

Listener wants MastGaane on the PC, driven by their assistant Dexter (and Alexa through Dexter).
`mastgaane/player.py`: the jockey loop with ffplay on the PC speakers (no window); pause =
psutil suspend; `jockey.resolve_focus()` turns a spoken wish into genre / language / era /
programme / singer. `/api/player/*` in the web app (see README).

**Evidence.** `pytest -q` → **49 passed** (+5 `tests/test_player.py`, real subprocess kill/suspend).
Live on 8788: play "ghazals" → Hindi link, then *Dillagi — Khalid Khan* (47 s heard); pause →
ffplay CPU 0.0 s over 3 s (status `stopped`), resume; next → *Us Mod Se Shuroo Karen — Jagjit Singh*;
stop → state stopped, 0 ffplay processes left.

## 2026-10-15 — FM Apple mode removed

The listener does not want the Apple Developer Program token route, so the untested `/fm` MusicKit
mode is gone; FM stays on the ad-free audio. Apple links, `apple_id` and the Shortcut route remain.

**Evidence.** `pytest -q` → **44 passed**; `/fm` has no Apple code (`grep -ci apple fm.html` → 0).

## 2026-10-15 — Apple Music: real links, exact Shortcut lines, FM Apple mode

Listener has an Apple Music subscription and wants both the playlist and FM playback there.
The Apple route had never run (0 links). Odesli, the backup resolver, now answers
`401 PUBLIC_API_ACCESS_DEPRECATED` — removed. iTunes Search took same-title songs by other singers
(Dillagi — Khalid Khan → Rahat Fateh Ali Khan's).

- `playlists/apple.py`: a hit needs the title and a shared credited name; second try with the title
  alone; calls spaced 3.2 s (Apple's ~20/min limit). `Song.apple_id` (catalog id) and
  `Song.apple_name` (Apple's spelling, used as the `import.txt` line). `/apple?resolve=1` runs in a
  background thread.
- `/fm`: " Apple Music" mode when `APPLE_DEV_TOKEN` is set — MusicKit JS v3 plays songs that have
  an `apple_id`; programmes / unmatched songs stay on the ad-free audio. Needs the $99 Apple
  Developer Program token: **not tested live**.

**Evidence.** `pytest -q` → **44 passed** (+4 `tests/test_apple.py`). Live `mastgaane sync -t apple`:
**108/121** approved songs linked, 13 no match, no 403s; `reports/apple_import.txt` 121 lines.
DB backup: `data/mastgaane.before-apple-2026-10-15.db`.

## 2026-10-15 — Reclassify results; LLM-only regional labels go to review

`reclassify` with gemma-4-12b flipped 46 songs to approved (Pardesi Pardesi, Dil Mera Tod Diya,
Tere Paas Main, Chupke Se, Darmiyaan, Marbo Re Sugwa Dhanukh…). It also called French, Telugu,
Malayalam and Punjabi songs Bhojpuri/Magahi/Awadhi "folk", and two heuristic (no-LLM) verdicts
slipped through. Now a keep that rests only on an LLM regional label, or on the no-LLM heuristic,
carries `[check] ` and stays *pending* (Discover / FM new finds) — `needs_review()`. 14 such songs
were moved from approved to pending in the live DB.

Discovery: round 1 used only 2 of 12 seeds (the 60-song budget went to the first radios, one seeded
from an LLM-only approval). Now each seed gets an equal quota, and seeds are hand-approved songs, then
songs a rules.yaml singer list vouches for, then LLM-only ones. Round 2: 12 radios — Chandan Tiwari /
Bhikhari Thakur bidesiya, Malini Awasthi kajri, Kalpana, 1990s ghazals (Pankaj Udhas 1997, Chandan
Dass 1991, Ustad Ahmed Hussain 1995, Ghulam Ali 1996), 1950s Rafi/Kishore — 47 queued, 13 filtered.

**Evidence.** `pytest -q` → **40 passed**. Live DB: approved 121, pending 15, rejected 121
(before today: approved 89, pending 4, rejected 164).

## 2026-10-15 — The last 25–30 years (programmes and songs), older kept

Listener wants programme recordings and songs from ~1996 onward, without dropping older classics.

- `archive.year_of()` reads broadcast dates from titles (`16 6 15`, `091216`, `21-08-98`, `1972`…);
  archive.org `date` is used otherwise (checked: it is the broadcast date, e.g. Jaimala Naushad →
  2005-07-31, uploaded 2025). `old_queries()` adds old-recording searches; up to 150 YouTube results.
- `/archive` decade filters + year badges; FM `_episode_item` picks a random era first, unheard first.
- `Song.year` (YouTube Music album year), `discover.fill_years()`; `seed_queries` in rules.yaml.

**Evidence.** `pytest -q` → **39 passed**. Live: Jaimala 4 / 18 / 6 / 80 / 24 episodes across
before-1996 / 1996–2005 / 2006–2015 / 2016+ / unknown; Geetmala reaches 1952; years found for
117/126 songs — approved: 35 before 1996, 20 1996–2005, 24 2006–2015, 37 2016+, 12 unknown.

## 2026-10-15 — MastGaane FM (jockey), auto-fetch, Naya kya hai

Listener asked for a video + radio jockey that keeps learning and brings new things. Choices
(asked 2026-10-15): natural Hindi voice (edge-tts), audio/video switchable, new finds mixed in,
weekly digest, auto-fetch new episodes.

- `mastgaane/jockey.py`: taste from the `play` table (like +3, full +1, skip −1, dislike −4 per
  artist/genre/programme), weighted pick, no repeats in the last 40, every 4th a discovery,
  Vividh-Bharati-like `SCHEDULE` slots for archive episodes; Hindi link lines from the LLM
  (prefetched while the current item plays) or templates; edge-tts voice cached in `data/jockey_voice/`.
- `/fm` page: one `<audio>` for voice + song (iPhone lock screen keeps playing), YouTube player in
  video mode, ⏭/👍/👎 → `/jockey/event`; skip uses an instant template link if the next item is not ready.
- `archive.fetch_new()` / `mastgaane fetch-episodes`; `mastgaane/digest.py` + `/naya` + `mastgaane digest`;
  both run from `run-all` (digest only when the last one is > 7 days old).

**Evidence.** `pytest -q` → **36 passed** (+7 in `tests/test_jockey.py`). Live on 8788: `/jockey/next`
returned "Chupke Se — A. R. Rahman…" with an LLM-written Devanagari link and a 68 KB voice mp3;
song audio was already prefetched (206 in 0.002 s). Headless Chrome on `/fm`: Start → voice link
played → song reached t=4 s → Skip → next link playing within 5 s; no page errors. The two test
skips were deleted from `play`. `match`: 114/115 approved songs now have a YouTube id.

## 2026-10-15 — Taste fixes, Radio, Discover, Archive

The listener said the app served nothing they liked. Causes found in `data/mastgaane.db`:
Bhojpuri was not an allowed language; qwen2.5:7b rejected good songs because "the caption is
not relevant"; `browser.json` (YouTube Music auth) is broken, so 0 of 89 approved songs had a
YouTube match and the Playlist player had nothing to play.

- Classifier: Bhojpuri/Awadhi/Maithili/Magahi allowed; `ghazal_artists`, `folk_artists`,
  `folk_keywords` rules; prompt forbids caption-based rejection. `.env` `LLM_MODEL=gemma-4-12b:latest`
  (9/10 on a hand-checked set vs 5/10 for qwen2.5:7b, ~20 s per song).
- `set_manual_status` marks hand decisions (`you: …`); `mastgaane reclassify` skips them.
- `mastgaane/radio.py` + `/radio` (hls.js for AIR m3u8 streams; directory refresh never blocks a page).
- `mastgaane/discover.py`: anonymous ytmusicapi search (`match`) and radio-based discovery
  (`discover`, also in `run-all`); `/discover` page with 👍/👎.
- `mastgaane/archive.py` + `/archive`: 8 programmes, ~350 episodes (archive.org + YouTube via
  ytmusicapi and yt-dlp search), title must name the programme, < 5 min dropped; YouTube audio
  fetched once and served with Range support.
- `mastgaane/buzzcaf_ports.py` was missing from the working tree; restored as a byte copy of
  `dexter/backend/buzzcaf_ports.py` (sha256 `51649b81…`).

**Evidence.** `uv run pytest -q` → **29 passed**. Live on port 8788: all pages 200; `/archive/ia/jaimala-k-j-yesudas.json`
→ 1 MP3; `/archive/yt/axyuY6yzeuI.audio` with `Range: bytes=0-1023` → 206 audio/mp4.
DB backup before reclassify: `data/mastgaane.before-reclassify-2026-10-15.db`.

## 2026-09-10 — Ports: nothing hardcoded (GUARDIAN_PLAN.md section 11, P2)

8787 was written down in five places — the CLI, the Apple phone page, the sync
report, the setup advice and the README. It now lives in one.

- `mastgaane/buzzcaf_ports.py` — byte-identical copy of
  `dexter/backend/buzzcaf_ports.py` (sha256 `51649b81…`, 18 243 bytes).
- `mastgaane/ports.py` — `preferred_port()` (`MASTGAANE_PORT`, else
  `DEFAULT_PORT = 8787`, the only literal), `choose_port()`, publish-when-healthy
  to `%LOCALAPPDATA%\Buzzcaf\ports.json`, `READY port=N`, `withdraw()`, and `running_base_url()` for the
  CLI's own advice.
- `mastgaane/cli.py serve` — `--port` is a preference; uvicorn is handed the app
  object (not an import string) so the bound port survives; the phone URL and the
  local URL are printed only once we answer, with the port we actually took.
- `mastgaane/web/app.py` — `/health` →
  `{"app":"mastgaane","status":"ok","port":n,"pid":n}`; the Apple page's
  `lan_url` uses the bound port instead of a literal.
- `mastgaane/playlists/sync.py` and `setup apple` name the running port, or the
  preferred one when nothing is running.

**Evidence.** `uv run pytest -q` → **24 passed** (16 before, +8 in
`tests/test_ports.py`). Live against the real ledger:

```
preferred 8787 free      -> bound 8787
  ledger  {"port":8787,"pid":51972,"health":"http://127.0.0.1:8787/health","extra":{}}
  health  {"app":"mastgaane","status":"ok","port":8787,"pid":51972}
preferred 8787 occupied  -> bound 8788   (the dummy listener kept 8787)
  health  {"app":"mastgaane","status":"ok","port":8788,"pid":46200}
after Ctrl-C             -> no "mastgaane" key in the ledger, both runs
```
