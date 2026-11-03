# Swap check: this build vs the older studio engine (blind A/B)

Built during the Meta Global AI Developer Hackathon (new work).

For each of the two sample videos and each of the 10 parameters, two outputs are shown as **A** and **B**. One is this build (the original Prysm pipeline running on Muse Spark with the extra evidence); the other is the older studio engine's output for the same video (a different, earlier scorer built on the same Meta models). Which is which is in the separate key file, [swap-check-key.md](swap-check-key.md); read the pairs first and judge them without it. Long fields are clipped at 700 characters.

What to look for: does the text read like the original Prysm voice (full-sentence problems, several actionable steps, quoted dialogue, no jargon), does it say something specific and true about the video, and does it avoid leftovers such as a stray `(at 12.3s)` suffix, evidence ids, internal edit names (`zoom_crop`) or broken emoji.

Scores are not comparable across the two engines: this build uses the original Prysm weights (the overall score is the weighted sum of the six scored parameters, whose weights add up to about 0.6, so it tops out near 60 by design of the original code); the older engine renormalised.

Sample videos used: Video-57419 (niches Fashion, Lifestyle) and Video-86506 (niches Dance, Entertainment). The sample files are not in this repo.

## Video-57419

### Video-57419 — Hook & Opening

**A**  
**Score: 85**
- *Problem:* Full drape movement not shown until dance at 24.5s, opening stays talking-head medium framing (at 1.5s)
- *Fix:* Cold-open with twirl
- *What is happening:* [0-3s] first 3 seconds on screen: A woman in an orange sheer saree and heavily embroidered blouse speaks directly to camera with animated hand gestures while on-screen text says stop wea… | [2-3.5s] on-screen text: "by stop wearing obvious designers" | [0-3s] spoken or sung in the first 3 seconds: This is exactly what I mean by stop wearing obvious designer | person: first seen 0s; visible in first 3 s: True; on screen 100% of the video; safe area (x1,y1-x2,y2, 0-1) 0.00,0.00-1.00,1.00
- *Why it matters:* The outfit is on screen from 0s with a contrarian text hook, which stops Fashion scrollers fast.
- *What to change:* Cold-open with twirl (0-3s)
- *How to do it:* Step 1 — Copy 28.0-30.3s twirl and prepend as 0-2s cold open under hook text, then cut back to 0s talking head
- *What to expect:* Shows finished look in motion within 2s while keeping hook line

**B**  
**Score: 64**
- *Problem:* Contrarian line fires but tight close-crop hides the full wow outfit until 3.4s, softening the fashion cold-open.
- *Fix:* Keep your contrarian formula but switch the visual to a full-torso cold open so outfit and line hit together.
- *What is happening:* 0-3s you are in the final orange sheer saree + purple-gold blouse in close talking-head, engaging to emphatic, saying 'This is exactly what I mean by stop wearing obvious designers' with word-by-word captions 'This / This is exactly / by stop wearing obvious designers'. Formula is contrarian / pattern interrupt, no pleasantry. 2 triggers fire: curiosity gap + controversy/contrarian — what designer, why stop obvious ones.
- *Why it matters:* viewers decide within 1.7 seconds whether to keep watching. Reels with 60%+ 3-second hold rates outperform weak holds by 5-10x in total reach. A compound hook (3+ triggers) is near-impossible to scroll past. A slow build-up (intro before the hook) loses 40-60% of viewers before the content even starts.
- *What to change:* Keep the same line and structure, but eliminate the visual slow-build by opening on the most striking transformation frame — full saree drape + blouse embroidery visible in frame one — to add immediate value as a third trigger.
- *How to do it:* Step 1 — Stay with contrarian cold-open formula: keep your voice line 'This is exactly what I mean by stop wearing obvious designers' exactly as is for 0-2.7s, do not re-record. Step 2 — Add immediate value trigger visually: in your editing app replace the 0-3s visual with your 3.47s-4.03s full outfit frame or zoom out the 0.5s frame to chest-to-drape so orange + purple hits instantly. Step 3 — First frame check: freeze at 0.0s — viewer should see your face + purple-gold blouse threadwork + orange drape fall together, not just face, to deliver color-contrast interrupt.
- *What to expect:* Adding the visual payoff to your existing curiosity + contrarian hook makes it compound — more non-followers stop because they get the what, the why, and the wow in the same second and stay to learn the designer name.

### Video-57419 — Retention & Pacing

**A**  
**Score: 83**
- *Problem:* Talk-through 15-24.5s stays in same bedroom angle explaining jewellery and drape (at 19s)
- *Fix:* Punch-in styling section
- *What is happening:* cuts per 10 s: 6.56; average shot length 1.46s; 24 cuts at (s): 3.37, 3.93, 5.43, 5.63, 8.37, 10.13, 11.97, 14.17, 15.7, 17.2, 17.7, 18.73, 20.27, 23.4, 25.13, 28, 29.1, 30.27, 30… | [7.5-12.5s] scene: Close-ups of the blouse on a hanger show purple threadwork with gold and purple hand embroidery and beading | [24.5-34.5s] scene: Music plays while she dances, twirls and poses to showcase the movement, color combo and drape from front, side and back | [19-24.5s] scene: She demonstrates the pre-draped convenience, adjusting the drape and explaining she can dance all night without worrying about pleats opening
- *Why it matters:* Quick cuts and a clear detail-to-fit-to-movement progression hold attention to the 10s dance payoff.
- *What to change:* Punch-in styling section (15-24.5s)
- *How to do it:* Step 1 — Add 110% zoom_crop punch-ins at 15.7s, 17.7s and 20.3s on cuts to break static angle
- *What to expect:* Adds visual change without reshoot during talk-heavy stretch

**B**  
**Score: 60**
- *Problem:* Hook promises an obvious-designer alternative but momentum dips at 10-12s during static macros without movement payoff.
- *Fix:* At 10-12s add a movement re-hook that visually proves your dance-all-night promise before the jewelry styling close.
- *What is happening:* Hook promise at 0-3s: stop obvious designers, I found a better pre-draped option. Open loop: who is Priyanshi Jain and is quality really couture? You develop with garment bag at 5.7s, thread couture line at 6-7.9s, blouse stitching macros at 8.4-10.2s. No clear 8-12s re-hook — 10.23s macro and 12.07s 'look at that colour combo' are informative but static. Energy curve confirms risk: 0-7s avg 0.2172 and 10-12s avg 0.3777 are well below hook energy. Payoff does land later with back-fit demo at 12-14s and minimal jewelry logic at 14-18s, so promise is delivered but mid dip risks drop.
- *Why it matters:* watch time is Instagram's confirmed #1 ranking signal (Adam Mosseri, Jan 2025). Completion rate directly determines non-follower reach. A video that loses viewers at 8s reaches half as many people as one that holds through to the end — even if every other parameter is identical.
- *What to change:* At 11s where 'being stitched / hand beaded' macros sit, insert your twirl / walk-out in the full drape to re-hook with motion, then return to macros — this bridges quality proof to your 20-23s 'dance all night without pleats falling apart' payoff.
- *How to do it:* Step 1 — At 8-9s strengthen the mid re-hook verbally: lift your voice emphasis on 'you can fully tell' and cut faster 8.47s to 8.78s to 10.23s (average shot is 1.5s, keep these under 1s) to avoid static linger.
- *What to expect:* Adding motion at 11s re-engages viewers who would drift during macros and carries them to your back-closure demo and styling conclusion, improving completion rate and non-follower push.

### Video-57419 — Shareability

**A**  
**Score: 62**
- *Problem:* Brand/designer name withheld behind comment gate, no product info on screen (at 35s)
- *Fix:* Reveal designer for sharers
- *What is happening:* [24.5-34.5s] scene: Music plays while she dances, twirls and poses to showcase the movement, color combo and drape from front, side and back | [34.5-37s] on-screen text: "Comment down to know the designer" | [3.5-7.5s] scene: She presents the outfit as a pre-draped saree from a designer and holds up a large yellow designer shipping bag
- *Why it matters:* Dance-proof drape demo is tag-a-friend worthy, but hiding the designer name blocks instant want-the-look shares.
- *What to change:* Reveal designer for sharers (34.5-37s)
- *How to do it:* Step 1 — Add text_overlay with designer name + price range at 34.5-37s and in caption
- *What to expect:* Lets viewers share/tag with actionable info

**B**  
**Score: 62**
- *Problem:* No clear DM-trigger — viewers love the take but are not told who to send it to.
- *Fix:* Add a verbal tag-your-friend provocation around your contrarian take to turn opinion into sends.
- *What is happening:* You have share seeds: contrarian take 'stop wearing obvious designers', emotional peak 'look at that colour combo It's to die for', relatable payoff 'I can actually dance and move all night long without worrying about my pleats falling apart'. No surprise twist or explicit tag moment, so it feels save-worthy but not yet DM-worthy.
- *Why it matters:* DM sends are weighted 3-5x more than likes for reaching new audiences (confirmed by Mosseri). Sends are THE viral mechanism — 694K reels are sent via DM every minute.
- *What to change:* Add a playful call-out verbal that gives viewers a reason to forward this to one specific friend who always wears mainstream designers to weddings.
- *How to do it:* Step 1 — At 11.3-14.1s when you say colour combo is to die for, glance to camera and add 'you know who you are' to create an inside-joke peak. Step 2 — Before you post, rehearse the delivery once so it sounds like banter, not an ad — festive fashion shares when it feels like group-chat advice.
- *What to expect:* More viewers will DM this to a friend planning festive / wedding looks, this becomes the kind of content people tag others in instead of just liking.

### Video-57419 — Audio & Music

**A**  
**Score: 67**
- *Problem:* Voice is clear and dominant but background Hindi vocal stays flat and does not lift the drape reveal or finale.
- *Fix:* Keep your voice lead and reshape the background music to swell under the twirl and finale so reveals feel intentional.
- *What is happening:* Viewers hear your English voiceover front and centre the whole time: 'pre-draped saree... India's first thread couture house... blouse being stitched and hand beaded... keep jewellery minimal... dance all night'. Voice is +2.8 dB over music, loudness -17.6 LUFS good, no distortion, quality good. Music yes — Majboor instrumental with female Hindi vocal fading in underneath near the end and briefly dominating outro. No sync hit on the 7.1s twirl or 12s fit demo, outro energy drops at 34-36s.
- *Why it matters:* trending audio gets ~80% more engagement. Users clicking to the audio page is an official ranking signal. Audio sets emotional tone and drives retention.
- *What to change:* This is working well for clarity — to push it to viral-ready, duck music lower under 0-14s explanation and lift it 3-4dB under 14-24s styling + dance movement so the payoff feels celebratory.
- *How to do it:* Step 1 — In your editing app lower background music during 0-10s voice proof so threadwork details stay crystal clear, keep your voice exactly as recorded. Step 2 — At 10-14s 'look at that colour combo' add a small beat lift and cut your twirl to the 89 BPM pulse so motion and music land together. Step 3 — At 19-24s under 'pre-draped I can dance all night' let music swell for 2s then dip back for voice, and trim the trailing 30-35s ramble so outro does not drag energy.
- *What to expect:* The audio will feel intentional rather than incidental — explanation stays intimate, reveal feels festive, and trending-style lift increases discoverability.

**B**  
**Score: 90**
- *Fix:* No audio fix needed
- *What is happening:* voice clarity 9/10; noise: background music; issues: none | music present: True; voice vs music: voice is much louder and dominates over background during spoken narration; music and sung voice dominate during the final song outro; problem… | music present: True; Upbeat, modern Indian pop/Bollywood music with a female vocalist singing in Hindi. Features a driving beat and melodic elements.; mood Energetic, celebratory,… | A/V sync ok: True; Lip movements generally align with spoken words.
- *Why it matters:* Crystal-clear voice over energetic celebratory music keeps styling explanation intelligible and mood high.
- *What to change:* No audio fix needed (0-36.6s)
- *How to do it:* Step 1 — Keep current mix; no edit required
- *What to expect:* Preserves clear voice-music balance

### Video-57419 — Trend Alignment

**A**  
**Score: 66**
- *Problem:* Format and content angle are on-trend for festive discovery, but audio is not riding a trending festive sound.
- *Fix:* Keep the emerging-designer discovery format and swap the background bed to a currently trending festive track that matches orange-purple celebratory energy.
- *What is happening:* (1) FORMAT — talking-head hook + garment bag reveal + macro craftsmanship proof + try-on fit demo + styled finale. This is emerging-designer find / anti-obvious luxury, which is a currently viral fashion format, much stronger than generic GRWM. (2) AUDIO — Majboor by Sheheryar Rehan, Zoha Waseem underneath voiceover, status unknown / not-trending for fashion, no transition sync to beat. (3) CONTENT ANGLE — stop obvious designers, India's first threadwork couture house, pre-drape you can dance in, minimal jewelry to keep focus on saree. Highly timely for wedding-festive season and quiet-luxury-alternative wave.
- *Why it matters:* trending FORMAT gets 2-3x more algorithmic distribution because Instagram's explore surface favors familiar content patterns. Trending AUDIO gets ~80% more engagement and clicking the audio drives discovery. Trending CONTENT ANGLE catches active search and hashtag waves. These three signals — not hashtags, not text overlays — are the actual trend distribution levers.
- *What to change:* Replace the background bed with What Jhumka? energy-style trending festive sound while keeping your voiceover 100% intact — that single audio swap gives algorithmic momentum without changing your story.
- *How to do it:* Step 1 — Keep format as is: hook + designer reveal + proof + styling payoff is exactly the discovery format performing now, do not recut to plain GRWM. Step 2 — In your editing app mute Majboor bed, search the trending festive sound What Jhumka? Step 3 — by Arijit Singh, Jonita Gandhi and lay it at 15-20% volume under your voice so voice stays +3dB clear. Step 4 — Keep angle sharp: lead description and first comment on India's first threadwork couture + pre-drape dance freedom — that alternative-to-mainstream angle is what festive shoppers are searching this season.
- *What to expect:* Switching to a trending festive audio triggers the audio discovery page, which surfaces this to new non-followers passively alongside other viral festive styling, while your format keeps you in the right explore cluster.

**B**  
**Score: 43**
- *Problem:* Identified track Majboor not in trending audio list (at 25s)
- *Fix:* Swap dance music to trending sound
- *What is happening:* LIVE TREND from a Google search today, for Fashion, Lifestyle on Instagram Reels: trending formats: Outfit transition reels with trending audio; Get-ready-with-me (GRWM) videos; M… | [19-24.5s] scene: She demonstrates the pre-draped convenience, adjusting the drape and explaining she can dance all night without worrying about pleats opening | LIVE TREND from a Google search today, for Fashion, Lifestyle on Instagram Reels: trending audio: Patient Zero - Taylor Swift; Autumn Colors - LuLuMusic; Housewife - Tate McRae; A… | identified track: Majboor by Sheheryar Rehan, Zoha Waseem; matched on rest_stem; play offset 63s
- *Why it matters:* Matches the GRWM videos format in style but misses the trending audio list, using Majboor instead of tracks like Patient Zero or Housewife.
- *What to change:* Swap dance music to trending sound (24.5-34.5s); Add versatility tag (0-36.6s)
- *How to do it:* Step 1 — Replace 24.5-34.5s music bed with trending audio e.g. Housewife - Tate McRae, keep voice-over muted there
- *What to expect:* Aligns showcase section with trending audio discovery

### Video-57419 — Visual Quality & Originality

**A**  
**Score: 85**
- *Problem:* Bedroom background with bed with green headboard and wooden door visible behind styling (at 4s)
- *Fix:* Tighten framing on talk sections
- *What is happening:* visual quality notes: Bright natural indoor lighting, vertical medium to full-body framing centered on subject, mostly stable with slight handheld movement, high resolution with c… | [7.5-12.5s] scene: Close-ups of the blouse on a hanger show purple threadwork with gold and purple hand embroidery and beading | [24.5-34.5s] scene: Music plays while she dances, twirls and poses to showcase the movement, color combo and drape from front, side and back
- *Why it matters:* Bright light and sharp embroidery close-ups plus 360 movement showcase sell fabric, colour and fit.
- *What to change:* Tighten framing on talk sections (0-7.5s)
- *How to do it:* Step 1 — Apply subtle zoom_crop 105-108% centered on subject for 0-7.5s to reduce bedroom background
- *What to expect:* Keeps focus on saree and blouse

**B**  
**Score: 77**
- *Problem:* Your opening in the orange saree looks crisp and vibrant, but the mid-video craftsmanship close-ups look a little softer and darker before you post.
- *Fix:* Clean up the 8-12s macro shots now so the purple-gold threadwork pops as hard as your opening full look.
- *What is happening:* This is original vertical content of you 100% of the time filling about 65% of the frame, no watermark or borders. You open 0-3s face-to-camera in the final orange sheer saree with purple-gold embroidered blouse saying 'This is exactly what I mean by stop wearing obvious designers' - lighting on your face is bright and clean. At 7.1s side twirl and 10.23s macro of 'being stitched' hand-beaded blouse the framing is tight and shows real detail, but that macro light dips darker than your opening so the gold beading loses a little punch.
- *Why it matters:* Instagram actively penalizes low-res, watermarked, and bordered content. Reposted content with 70%+ visual similarity gets flagged. 10+ reposts in 30 days = excluded from recommendations entirely.
- *What to change:* Keep this exact story and order, just lift brightness and sharpness on the 8.4-12s product close-ups and hold a clean final full-length hold at the end.
- *How to do it:* Step 1: In your editing app select the clips from 8.4s to 12.0s where you show 'and you can fully tell' the stitching and bump up brightness and sharpness just until the gold threads glow like they do at 7.1s. Step 2: Crop the 12.07s back-closure demo slightly tighter so your hands and the blouse fit stay centered and nothing distracts. Step 3: Freeze the last dancing full look for 0.5s longer before you post so viewers get a clear final wow frame to remember.
- *What to expect:* The video will feel more professional and the craftsmanship story will land harder because viewers can actually see the hand-beading quality you are talking about.

### Video-57419 — On-Screen Text

**A**  
**Score: 74**
- *Problem:* Fragmented phrases like 'being stitched and hand' split across cuts are hard to read (at 10.5s)
- *Fix:* Consolidate mid captions
- *What is happening:* [2-3.5s] on-screen text: "by stop wearing obvious designers" | [12.5-13.5s] on-screen text: "that colour combo" | [34.5-37s] on-screen text: "Comment down to know the designer" | [10-11.5s] on-screen text: "being stitched and hand"
- *Why it matters:* Hook, styling reasons and CTA are captioned word-for-word, aiding muted viewing.
- *What to change:* Consolidate mid captions (8.5-12.5s)
- *How to do it:* Step 1 — Merge 8.5-12.5s captions into two lines: 'hand-beaded blouse' / 'look at that colour combo' with larger font clear of face
- *What to expect:* Improves readability on mute

**B**  
**Score: 72**
- *Problem:* This is working well early — captions already carry your hook — to push it to viral-ready you just need to extend that same caption coverage to your styling payoff.
- *Fix:* Add matching captions where they are missing at 17-24s so mute viewers still get 'I kept the jewellery very minimal' and 'I can actually dance'.
- *What is happening:* (1) CAPTION COVERAGE — you already have clear bottom captions from 0.0s 'This' through 15.8s 'So to keep all the attention on the saree' covering 'It's a pre draped saree' and 'being stitched' - great for mute viewers. Gap is 17.0-24.6s where you say 'I kept the jewellery very minimal on purpose' and 'I can actually dance and move along all night long without worrying about my pleats falling apart' with no caption listed. (2) PATTERN INTERRUPTS — no bold mid-video callout yet at your 8-12s re-hook or colour-combo punchline. (3) STYLE/READABILITY — existing captions are large, centered-bottom, high contrast and easy to read, not hidden by UI. (4) OVERLOAD CHECK — no overload, only 1 caption l…
- *Why it matters:* 50%+ of Reels are watched mute — captions are not optional for speech-heavy content. Pattern interrupt text overlays at key mid-video moments (stat callouts, punchlines, reveals) increase retention by 40–60% and saves by 40%. But text overload (3+ elements in one zone, or a new Hook/Attention overlay on top of existing captions + voiceover) causes cognitive overload and REDUCES retention. The goal is strategic placement, not maximum coverage.
- *What to change:* Keep all existing 0-15.8s captions exactly as they are, add missing captions at 17-24s in the same style, and add one Context label for the designer that does not fight the captions.
- *How to do it:* Step 1 — At 17.0s to 18.7s add bottom captions in your same font for 'I kept the jewellery very minimal on purpose' and keep it on screen minimum 2 seconds. Step 2 — At 5.73s where you hold the garment bag and say 'Who runs' add a small top-corner Context tag 'Designer: Priyanshi Jain' - small, corner zone separate from bottom captions so you never have more than 2 elements and you stay out of the redundancy timestamps with a new hook overlay.
- *What to expect:* Mute viewers will now follow the key styling argument at 17-24s where no caption existed, and the small designer corner tag gives a save-worthy reference without creating visual noise over your existing captions.

### Video-57419 — Save Potential

**A**  
**Score: 62**
- *Problem:* Viewers love the colour combo and designer find but they have nothing quick to save and refer back to later.
- *Fix:* Add a quick spoken recap right after your dance demo listing the 3 save-worthy details before you post.
- *What is happening:* You do teach real reference value in speech: 'It's a pre draped saree from a designer who runs India's first dedicated thread couture house', 'look at that colour combo It's to die for', 'to keep all the attention on the saree especially the blouse I kept the jewellery very minimal', and 'I can actually dance and move along all night long without worrying about my pleats falling apart'. But it flies by as story with no consolidated tip moment, so there is no clear reason to hit save.
- *Why it matters:* saves are a confirmed 'heavy' interaction signal — 57% of viewers save content they find valuable. A post with 50 saves and 20 shares can outrank one with 200 likes but 0 shares.
- *What to change:* Keep your story exactly, just speak a 3-point styling cheat-sheet at the payoff so bridesmaids and festive shoppers save it for their own sourcing.
- *How to do it:* Step 1 — Deliver it in your current explanatory tone, no need to re-shoot the outfit, just trim it tight to under 4 seconds. Step 2 — Leave your dancing outro after it so the recap feels like the takeaway before the fun close.
- *What to expect:* Viewers will save this to reference later for festive shopping because the designer name, colour formula, and minimal-jewellery rule are now packaged as a usable checklist instead of scattered lines.

**B**  
**Score: 54**
- *Problem:* No designer, product or jewellery names listed for later reference (at 30s)
- *Fix:* Add saveable details caption
- *What is happening:* [19-24.5s] scene: She demonstrates the pre-draped convenience, adjusting the drape and explaining she can dance all night without worrying about pleats opening | [17.5-19s] on-screen text: "jewellery very minimal on purpose" | [34.5-37s] on-screen text: "Comment down to know the designer"
- *Why it matters:* Drape hack and minimal-jewellery tip are save-worthy, but missing designer names lower save value.
- *What to change:* Add saveable details caption (0-36.6s)
- *How to do it:* Step 1 — Add caption listing: saree type + blouse work + jewellery + drape tip for 0-36.6s
- *What to expect:* Gives reason to save for party styling

### Video-57419 — Replay Value

**A**  
**Score: 64**
- *Problem:* Your twirl, macro stitching, and dance move are rewatchable, but the video ends loose so there is no pull to watch again.
- *Fix:* Tighten the end into a loop back to your opening pose so the final dance flows straight into your hook line.
- *What is happening:* There is no twist ending yet, but there are dense rewatch moments: the 3.47s-4.03s full outfit reveal, the 10.23s 'being stitched' macro where viewers want to pause on threadwork, and the high-energy 22-26s section where you move and dance to prove 'I can actually dance and move along all night'. Right now you start close talking-head saying 'This is exactly what I mean' and you end on a loose outro around 30-35s, so it does not circle back.
- *Why it matters:* replays count as additional watch time — Instagram's #1 signal. 48% of users replay entertaining content. Loopable content multiplies total view time.
- *What to change:* Create a before/after loop using shots you already have - end on the same full-look framing as your 0-3s opening.
- *How to do it:* Step 1 — Keep the music bed continuous under that cut so there is no silent gap breaking the loop.
- *What to expect:* The loop will feel seamless and trigger replays because viewers rewatch to compare the macro bead detail to the full moving look and to catch the colour combo again.

**B**  
**Score: 68**
- *Problem:* No before-after or transition to rewatch; first 24s is explanation (at 12s)
- *Fix:* Loop the twirl
- *What is happening:* [24.5-34.5s] scene: Music plays while she dances, twirls and poses to showcase the movement, color combo and drape from front, side and back | cuts per 10 s: 6.56; average shot length 1.46s; 24 cuts at (s): 3.37, 3.93, 5.43, 5.63, 8.37, 10.13, 11.97, 14.17, 15.7, 17.2, 17.7, 18.73, 20.27, 23.4, 25.13, 28, 29.1, 30.27, 30… | tempo 89.3 bpm; integrated loudness -17.57 LUFS; silence ratio 0.02
- *Why it matters:* Twirl showcase rewards one rewatch, but long talk-through without transformation limits looping.
- *What to change:* Loop the twirl (28-32.5s)
- *How to do it:* Step 1 — Speed_ramp 28-32s to beat at 89.3 bpm and loop 30.27-32.27s cut twice
- *What to expect:* Creates satisfying replayable movement beat

### Video-57419 — Call to Action

**A**  
**Score: 83**
- *Problem:* CTA appears only in final 2.5s, no earlier teaser to comment (at 35s)
- *Fix:* Tease CTA earlier
- *What is happening:* call to action: Comment down to know the designer | [34.5-37s] on-screen text: "Comment down to know the designer" | [34.5-37s] scene: She smiles and gestures toward camera as on-screen text asks viewers to comment to know the designer
- *Why it matters:* Verbal, on-screen and gestured 'comment to know designer' align at the end to convert curiosity.
- *What to change:* Tease CTA earlier (7.5-12.5s)
- *How to do it:* Step 1 — Add small text_overlay 'designer at end' at 7.5-12.5s during blouse close-ups
- *What to expect:* Primes commenting intent before drop-off

**B**  
**Score: 38**
- *Problem:* There is no clear ask after your payoff, so viewers who love the look scroll without commenting or sharing.
- *Fix:* Add one specific verbal ask right after your pleats line to drive comments before you post.
- *What is happening:* There is no verbal or on-screen CTA detected. You close your styling story with 'So to keep all the attention on the saree especially the blouse I kept the jewellery very minimal' and 'I can actually dance and move along all night long without worrying about my pleats falling apart' then it drifts to outro chatter with no direction to follow, save, share, or comment, and nothing placed after the payoff.
- *Why it matters:* CTAs directly drive the engagement actions the algorithm measures. 'Comment your city' drives more comments than 'like and follow'. 'Send this to someone who...' triggers the #1 viral signal — DM shares.
- *What to change:* Add a single festive-specific verbal CTA spoken in your final look after the dance proof.
- *How to do it:* Step 1 — Right after you say 'without worrying about my pleats falling apart' speak this exact line on camera: 'Would you wear this orange-purple combo for Diwali night - tell me yes or no in comments and send this to someone who always wears obvious designers'. Step 2 — Say it with the same emphatic energy as your opening 'stop wearing obvious designers' and look straight in lens. Step 3 — Stop there - do not add a second ask, let this be the one dominant close before your final twirl.
- *What to expect:* More viewers will comment with yes/no and tag friends because you gave them a specific festive occasion and a person to send it to instead of a generic follow ask.

## Video-86506

### Video-86506 — Hook & Opening

**A**  
**Score: 83**
- *Problem:* Sideways presentation weakens first impression (at 0.5s)
- *Fix:* Punch-in hook crop
- *What is happening:* [0-3s] first 3 seconds on screen: A young woman in a white graphic tee lip-syncs close to camera to a fast upbeat Punjabi song, pointing and gesturing expressively | [0-6.6s] scene: Woman in white MALIBU graphic t-shirt stands in living room lip-syncing to music, pointing at camera, moving hands and adjusting hair while looking at lens | person: first seen 0s; visible in first 3 s: True; on screen 100% of the video; safe area (x1,y1-x2,y2, 0-1) 0.00,0.00-1.00,1.00 | [0-3s] audio in the first 3 seconds: नेरो सलवार सूट रेड मुंडिया
- *Why it matters:* Instant song hook and direct eye-contact keep Indian creator viewers past 2s.
- *What to change:* Punch-in hook crop (0-3s)
- *How to do it:* Step 1 — zoom_crop 0-3s to 110% centered on face to emphasize glance and gesture
- *What to expect:* Stronger eye-contact and energy in feed

**B**  
**Score: 55**
- *Problem:* Single-trigger performance opening with no tease of the outfit payoff, so no compound curiosity in the first 3 seconds.
- *Fix:* Keep the casual-tee opening but switch to a cold-open beat hit plus a curiosity-gap hook text teasing the reveal.
- *What is happening:* In 0-3s you are chest-up in the casual white graphic tee, neutral to expressive, lip-syncing 'nero salwar suit red mundyaan' direct to camera with no on-screen text. Formula used is direct-address performance / none — not a cold open peak move and not a pattern interrupt. 1 trigger fires: immediate value from dance energy, but curiosity gap, social proof, FOMO, and controversy are missing.
- *Why it matters:* viewers decide within 1.7 seconds whether to keep watching. Reels with 60%+ 3-second hold rates outperform weak holds by 5-10x in total reach. A compound hook (3+ triggers) is near-impossible to scroll past. A slow build-up (intro before the hook) loses 40-60% of viewers before the content even starts.
- *What to change:* Stay in the tee for the hook but add a second trigger — curiosity about the transformation — and cut the soft 0.0s frame so your sharpest move lands on beat 1.
- *How to do it:* Step 1 — Use cold open in your editing app — trim the 0.0s soft frame and start at 0.5-1.0s where you are expressive on 'nero salwar suit' with a sharp head-pop. Step 2 — Add curiosity-gap hook text at 0-3s top-center like Wait for the white suit look that teases the 6.5s flip without showing it. Step 3 — Make frame 1 tight chest-up, bright on your face, eye contact + slight smile in the tee so the first frame reads as performance energy, not setup.
- *What to expect:* Adding curiosity to your existing performance energy makes it compound — more non-followers stop because they get both instant dance vibe and a reason to wait for the 6.5s payoff. NO invented percentages.

### Video-86506 — Retention & Pacing

**A**  
**Score: 57**
- *Problem:* Long static take 0-6.6s with no cut (at 3s)
- *Fix:* Tighten opening take
- *What is happening:* [6.6-12s] scene: Quick outfit transition to cream traditional top with red floral embroidery, she continues lip-syncing, smiles, tosses hair and poses to show the outfit | cuts per 10 s: 1.39; average shot length 5.39s; 3 cuts at (s): 8.93, 11.97, 14.53 | [0-6.6s] scene: Woman in white MALIBU graphic t-shirt stands in living room lip-syncing to music, pointing at camera, moving hands and adjusting hair while looking at lens
- *Why it matters:* Beat-matched cuts and a mid-video reveal carry watch-through; long static takes cause drop-off.
- *What to change:* Tighten opening take (0-6.6s); Add beat speed pulse (8.93-14.53s)
- *How to do it:* Step 1 — trim 0-6.6s to 0-3.5s, cut on lyric at ~2s to jump to outfit tease
- *What to expect:* Faster pacing into reveal

**B**  
**Score: 62**
- *Problem:* No open loop to the 6.5s flip and energy dips at 5.5-6.5s and 18-21s that risk drop before and after the payoff.
- *Fix:* At 5.5s tighten into the hand-cover and at 18s lift the closing so the white-outfit payoff plays full energy.
- *What is happening:* Hook promise is continuous high-energy Punjabi dance plus a transformation from tee to white traditional with red. There is no open loop in the first 5s telling viewers a change is coming. Mid-video re-hook exists at 6.5-9.0s via the hand-cover blur and reveal at 9.03s in white, which lifts energy. Most likely drop is at 5.5-6.5s where movement softens right before the transition and again at 17-21.6s where energy falls and music fades during your smiling hair-flip close. You do deliver the promise fully with continued dance to 'vekh vekh vekh mundiyan de jaan nikle'.
- *Why it matters:* watch time is Instagram's confirmed #1 ranking signal (Adam Mosseri, Jan 2025). Completion rate directly determines non-follower reach. A video that loses viewers at 8s reaches half as many people as one that holds through to the end — even if every other parameter is identical.
- *What to change:* At 5.5-6.5s remove the soft hold so the hand-cover hits exactly on 'vich naag jade' and at 18-21s trim the fade so the joyful close matches hook energy.
- *How to do it:* Step 1 — At 9-12s strengthen the existing re-hook by punching in 5% on the reveal in white on 'White Gold di vang' and holding the smile beat for 2 seconds. Step 2 — At 18-21s trim the tail fade and end on your 19.68s joyful hair-flip frame so closing energy matches the opening and loops cleanly.
- *What to expect:* Tightening at 5.5s means viewers who would dip before the flip stay for the reveal, and lifting the close pushes completion so Instagram shows this to more non-followers. NO invented percentages.

### Video-86506 — Shareability

**A**  
**Score: 58**
- *Problem:* Fun transformation but no clear DM-a-friend moment or relatable wink beyond the outfit switch.
- *Fix:* Play up the cheeky 'mundiyan de jaan nikle' wink and confident final pose as the send-to-friend flex.
- *What is happening:* You have a mild surprise at 6.5s with the hand-cover to white traditional with red, and an emotional peak at 18.5-19.68s with happy to joyful smiles and hair flip to 'jadon vi jatt ban ke'. There is no tag-your-friend cue, no relatable punchline, and no controversy — it reads as a polished vibe dance rather than a must-send moment.
- *Why it matters:* DM sends are weighted 3-5x more than likes for reaching new audiences (confirmed by Mosseri). Sends are THE viral mechanism — 694K reels are sent via DM every minute.
- *What to change:* Lean into the playful flex of the 'vekh vekh' lyric with a direct camera wink and hold the final confident pose to invite duets and tags.
- *How to do it:* Step 1 — At 15.6s on 'vekh vekh vekh' add a cheeky side-glance and smile to camera to sell the lyric as a relatable flex before you post.
- *What to expect:* More viewers will DM this to a friend as festive inspo and this becomes the kind of transformation people tag others in. NO invented percentages.

**B**  
**Score: 75**
- *Problem:* No desi humour or relatable gag evidenced (at 10s)
- *Fix:* Add share prompt caption
- *What is happening:* [6.6-12s] scene: Quick outfit transition to cream traditional top with red floral embroidery, she continues lip-syncing, smiles, tosses hair and poses to show the outfit | music present: True; Upbeat Punjabi folk-pop song with a strong rhythmic foundation, featuring traditional Indian percussion and a melodic synth/keyboard line.; mood Energetic, li… | identified track: Mitran De Boot by Jazzy B, Kaur-B; matched on mix; play offset 31.98s
- *Why it matters:* Aspirational desi look with confident trending-sound lip-sync drives shares.
- *What to change:* Add share prompt caption (18-21.5s)
- *How to do it:* Step 1 — Add caption 18.0-21.5s: 'Which look - casual or desi? Share with friend'
- *What to expect:* Prompts shares without reshoot

### Video-86506 — Audio & Music

**A**  
**Score: 88**
- *Problem:* Music dominates slightly over voice (at 5s)
- *Fix:* Polish voice-music balance
- *What is happening:* music present: True; Upbeat Punjabi folk-pop song with a strong rhythmic foundation, featuring traditional Indian percussion and a melodic synth/keyboard line.; mood Energetic, li… | A/V sync ok: True; Lip movements of the person in the video align perfectly with the pre-recorded song. | identified track: Mitran De Boot by Jazzy B, Kaur-B; matched on mix; play offset 31.98s | music present: True; voice vs music: music dominates slightly, voice is clear and sits on top of loud bhangra-style music but does not overpower it; problems: none
- *Why it matters:* A recognisable Punjabi track well-mixed under lip-sync drives energy and shares.
- *What to change:* Polish voice-music balance (0-21.56s)
- *How to do it:* Step 1 — audio_balance lift voice +1.5dB 0-21.5s, limit peaks to -16 LUFS
- *What to expect:* Clearer lip-sync without losing bhangra punch

**B**  
**Score: 70**
- *Problem:* Great song fit but music sits slightly over your lip-sync voice and the ending fades soft.
- *Fix:* Keep Mitran De Boot and rebalance so your voice cuts through while the beat stays punchy.
- *What is happening:* Viewers hear Mitran De Boot Bhangra beat throughout with your clear Hindi/Punjabi lip-sync to 'nero salwar suit... White Gold di... vekh vekh' sitting just under the music. Music and voice belong together and no harsh noise is heard, but the second half music swells over the vocal and the last 3 seconds lose drive as the track fades.
- *Why it matters:* trending audio gets ~80% more engagement. Users clicking to the audio page is an official ranking signal. Audio sets emotional tone and drives retention.
- *What to change:* Lift your vocal presence through 9-16s and tighten the transition hit at 6.5s to land exactly on the beat before you post.
- *How to do it:* Step 1 — In your editing app lower the backing track just a touch under 9-16s so 'White Gold di vang' and 'vekh vekh' read crisp.
- *What to expect:* The audio will feel intentional rather than incidental and the lyric-to-outfit sync becomes the memorable hook that aids discoverability. NO invented percentages.

### Video-86506 — Trend Alignment

**A**  
**Score: 70**
- *Problem:* No evidence of current film trend beyond catalog track (at 10s)
- *Fix:* Tag track on screen
- *What is happening:* identified track: Mitran De Boot by Jazzy B, Kaur-B; matched on mix; play offset 31.98s | setting: a modern home living room with patterned curtains, sofa, grey wall with orange-lit niches and a framed family photo; subject: a young woman with long dark wavy hair, firs… | music present: True; Upbeat Punjabi folk-pop song with a strong rhythmic foundation, featuring traditional Indian percussion and a melodic synth/keyboard line.; mood Energetic, li…
- *Why it matters:* no live trend data in Studio v1. Judged only from track and dance lip-sync format cues.
- *What to change:* Tag track on screen (0-3s)
- *How to do it:* Step 1 — text_overlay 0-3s add 'Mitran De Boot 🔥 Outfit Change' in top safe area
- *What to expect:* Clearer trend participation signal

**B**  
**Score: 68**
- *Problem:* FORMAT and CONTENT ANGLE are aligned with viral dance transitions, but AUDIO is an older catalogue track with unknown current trend status.
- *Fix:* Keep the dance-transition FORMAT and festive suit CONTENT ANGLE and keep this AUDIO for lyric-match over chasing a generic trending beat.
- *What is happening:* (1) FORMAT — dance performance + before-after outfit change via hand-cover transition, which is a currently viral FORMAT for Dance/Entertainment because continuous movement plus reveal holds watch time. (2) AUDIO — Mitran De Boot by Jazzy B, Kaur-B, status unknown/not-current-chart with no trend data provided, but STYLE fits Bhangra dance wave and lyric content matches the visuals. (3) CONTENT ANGLE — desi suit glow-up timed to suit lyrics, evergreen yet culturally timely for festive and wedding season, riding active desi-transition interest.
- *Why it matters:* trending FORMAT gets 2-3x more algorithmic distribution because Instagram's explore surface favors familiar content patterns. Trending AUDIO gets ~80% more engagement and clicking the audio drives discovery. Trending CONTENT ANGLE catches active search and hashtag waves. These three signals — not hashtags, not text overlays — are the actual trend distribution levers.
- *What to change:* Keep this FORMAT and ANGLE and double down on lyric-sync as your trend edge — the suit lyric to suit reveal is stronger than swapping to an unrelated trending sound.
- *How to do it:* Step 1 — Keep the AUDIO Mitran De Boot and use the official sound listing so you collect audio-page discovery from others using the same track. Step 2 — Sharpen the CONTENT ANGLE toward festive suit inspo by styling the white with red accents to pop on camera for wedding-season search.
- *What to expect:* Sticking with this familiar transition format means Instagram will show this alongside other viral dance-transition content, and the lyric-matched audio triggers audio-page discovery to new non-followers passively. NO invented percentages.

### Video-86506 — Visual Quality & Originality

**A**  
**Score: 76**
- *Problem:* Your reveal outfit is slightly darker and softer than your opening, so the transformation doesn't pop as hard as it could before you post.
- *Fix:* Clean up the light and sharpness on the post-change clips so the white suit with red accents pops.
- *What is happening:* This is 100% original you on screen the whole time in vertical, filling about 45% of the frame - starting in a casual white graphic tee lip-syncing 'नेरो सलवार सूट रेड मुंड्यां' at 0-6s, hand-cover blur jump at 6.5s, then reveal in white traditional with red floral details at 9s-21s smiling and dancing to 'वेख वेख वेख वेख मुंडियां दे जान निकले'. No watermark, no borders, no repost flags, 3 clean cuts at 8.9s, 12.0s, 14.5s.
- *Why it matters:* Instagram actively penalizes low-res, watermarked, and bordered content. Reposted content with 70%+ visual similarity gets flagged. 10+ reposts in 30 days = excluded from recommendations entirely.
- *What to change:* Keep this exact structure - just even out exposure between look 1 and look 2 and tighten the 6.5s transition blur before you post.
- *How to do it:* Step 1 — Export vertical 1080x1920 with no border before you post.
- *What to expect:* The video will feel more professional and the before-after will hit harder - Instagram will treat this as clean original content, not flagged or soft.

**B**  
**Score: 34**
- *Problem:* Video presented sideways rotated 90 degrees (at 0s)
- *Fix:* Fix orientation
- *What is happening:* visual quality notes: bright indoor artificial lighting, vertical video presented sideways rotated 90 degrees, medium close-up framing, slight handheld shake, decent resolution | [6.6-12s] scene: Quick outfit transition to cream traditional top with red floral embroidery, she continues lip-syncing, smiles, tosses hair and poses to show the outfit
- *Why it matters:* Bright flattering framing and clean transitions sell fashion; poor orientation kills watchability.
- *What to change:* Fix orientation; Stabilize and reframe (0-21.56s)
- *How to do it:* Step 1 — direction unknown: check a frame before applying - rotate 90 degrees clockwise (ffmpeg transpose=1) or counterclockwise (transpose=2) to make head upright
- *What to expect:* Upright watchable vertical framing

### Video-86506 — On-Screen Text

**A**  
**Score: 16**
- *Problem:* No lyric captions or hook text described (at 1.5s)
- *Fix:* Add lyric captions
- *What is happening:* [0-3s] first 3 seconds on screen: A young woman in a white graphic tee lip-syncs close to camera to a fast upbeat Punjabi song, pointing and gesturing expressively | [0-6.6s] scene: Woman in white MALIBU graphic t-shirt stands in living room lip-syncing to music, pointing at camera, moving hands and adjusting hair while looking at lens
- *Why it matters:* Hindi lyric captions and hook text help sound-off viewers follow the song.
- *What to change:* Add lyric captions (0-6s)
- *How to do it:* Step 1 — Add captioned lyrics 0.0-6.0s for opening line, keep off face safe area
- *What to expect:* Improves comprehension sound-off

**B**  
**Score: 45**
- *Problem:* There is zero text on screen right now, so muted viewers get no context for the 'नेरो सलवार सूट' transformation.
- *Fix:* Add two minimal mid-video labels to mark the change and highlight the lyric payoff.
- *What is happening:* (1) CAPTION COVERAGE - no captions anywhere, muted viewers hear nothing of 'दी कमान जिथे रेड मुंडे रिंग विच नाग जड़े सवा लाख दे' and 'व्हाइट गोल्ड दी वंग'. (2) PATTERN INTERRUPTS - no bold callout at the 6.5s hand-cover transition or at the 15.6s 'वेख वेख वेख' peak where retention needs a re-hook. (3) STYLE - nothing to judge yet. (4) OVERLOAD CHECK - no overload, screen is clean.
- *Why it matters:* 50%+ of Reels are watched mute — captions are not optional for speech-heavy content. Pattern interrupt text overlays at key mid-video moments (stat callouts, punchlines, reveals) increase retention by 40–60% and saves by 40%. But text overload (3+ elements in one zone, or a new Hook/Attention overlay on top of existing captions + voiceover) causes cognitive overload and REDUCES retention. The goal is strategic placement, not maximum coverage.
- *What to change:* Add one Context overlay at the transition and one Reaction overlay at the lyric peak - both in different zones, each staying 2+ seconds, before you post.
- *How to do it:* Step 1 — Use bold white text with black stroke, large enough to read on a phone, max 2 elements at once.
- *What to expect:* Mute viewers will now follow the transformation at 6.5s and the lyric punch at 15.6s re-hooks viewers who were about to drop - this is where retention typically falls for this format.

### Video-86506 — Save Potential

**A**  
**Score: 48**
- *Problem:* Right now this is pure performance with no reference detail, so there is no reason to save it.
- *Fix:* Add outfit reference value in your caption before you post so this becomes save-worthy.
- *What is happening:* You dance and lip-sync 'व्हाइट गोल्ड दी वंग वीड़ी विच शणके वेख' in two looks - casual white tee then white traditional with red accents - but you never teach, list, or give a tip viewers would come back to.
- *Why it matters:* saves are a confirmed 'heavy' interaction signal — 57% of viewers save content they find valuable. A post with 50 saves and 20 shares can outrank one with 200 likes but 0 shares.
- *What to change:* Keep the video as-is and make the caption the save trigger with a quick look breakdown before you post.
- *How to do it:* Step 1 — Before you post, write your caption as Look 1: white graphic tee + natural hair / Look 2: white suit + red dupatta + jhumka. Step 2 — Add one line 'Save for Karwa / festive inspo' to give viewers a reason. Step 3 — Pin a comment after posting: 'Which detailing you want - suit link or makeup?'
- *What to expect:* Viewers will save this to reference later for festive outfit inspo instead of just liking and scrolling.

**B**  
**Score: 65**
- *Problem:* No designer, price or product tags (at 11s)
- *Fix:* Add styling save cue
- *What is happening:* [6.6-12s] scene: Quick outfit transition to cream traditional top with red floral embroidery, she continues lip-syncing, smiles, tosses hair and poses to show the outfit | setting: a modern home living room with patterned curtains, sofa, grey wall with orange-lit niches and a framed family photo; subject: a young woman with long dark wavy hair, firs… | [12-22s] scene: She keeps performing to the song in the embroidered outfit, dancing slightly, gesturing, touching hair, smiling and turning to display the look until the video…
- *Why it matters:* Outfit and styling details get saved for later shopping.
- *What to change:* Add styling save cue (12-18s)
- *How to do it:* Step 1 — text_overlay 12-18s add 'Cream + Red Phulkari ✨ Details in caption'
- *What to expect:* Higher saves for outfit reference

### Video-86506 — Replay Value

**A**  
**Score: 70**
- *Problem:* Only 3 cuts, second half repeats same moves (at 16s)
- *Fix:* Create tight loop
- *What is happening:* [6.6-12s] scene: Quick outfit transition to cream traditional top with red floral embroidery, she continues lip-syncing, smiles, tosses hair and poses to show the outfit | cuts per 10 s: 1.39; average shot length 5.39s; 3 cuts at (s): 8.93, 11.97, 14.53 | A/V sync ok: True; Lip movements of the person in the video align perfectly with the pre-recorded song.
- *Why it matters:* Beat-synced transition rewards a second watch, but long static tail limits looping.
- *What to change:* Create tight loop (14.5-21.567s)
- *How to do it:* Step 1 — Trim 14.5-21.5s tail and loop end pose to opening beat at 0.0s
- *What to expect:* Improves seamless replay

**B**  
**Score: 62**
- *Problem:* The outfit snap at 6.5s and smile peak at 18.5s are rewatchable, but the ending fades and doesn't invite a replay.
- *Fix:* Tighten the ending to the beat so the final 'जदों भी जट यथ बन के' smile hits and loops back with energy.
- *What is happening:* You have a natural rewatch moment - hand-cover at 6.5s turning into the white suit reveal at 9.03s-9.5s, plus peak energy at 15s and playful to joyful smiles at 18.5s-19.68s singing 'निकले जदों भी जट यथ बन के'. But you end on a held smile at 21.18s with falling energy, and because look 1 vs look 2 differ, it can't loop seamlessly.
- *Why it matters:* replays count as additional watch time — Instagram's #1 signal. 48% of users replay entertaining content. Loopable content multiplies total view time.
- *What to change:* Keep the story order - just cut the tail on the beat and add a quick visual snap at the peak before you post.
- *How to do it:* Step 1: In your editing app, trim the last 0.5s after 21.18s so you end right on your confident smile, not on the fade. Step 2: Add a tiny zoom punch at 15s-15.5s where energy peaks to make that hair-flip moment worth rewatching. Step 3: Check playback - your 19.68s joyful face should cut cleanly so auto-replay feels instant.
- *What to expect:* Viewers will rewatch to catch the 6.5s snap again and the loop will feel tighter and trigger replays.

### Video-86506 — Call to Action

**A**  
**Score: 7**
- *Problem:* No call to action in entire video (at 20s)
- *Fix:* Add casual comment CTA
- *What is happening:* call to action: none
- *Why it matters:* A short casual CTA like comment for designer fits this style and lifts comments.
- *What to change:* Add casual comment CTA (18-21.5s)
- *How to do it:* Step 1 — Add caption/text_overlay 18.0-21.5s: 'Comment for outfit link • Follow for more'
- *What to expect:* Lifts comments and follows

**B**  
**Score: 38**
- *Problem:* There is no CTA right now - you end smiling at 21.18s with no prompt to comment, share or follow.
- *Fix:* Add one specific verbal + caption CTA after the payoff asking viewers to pick their look.
- *What is happening:* No verbal or on-screen CTA detected - you perform through 'वेख वेख वेख' to 'निकले जदों भी जट यथ बन के' and close with happy smiles, but you never tell viewers what to do next.
- *Why it matters:* CTAs directly drive the engagement actions the algorithm measures. 'Comment your city' drives more comments than 'like and follow'. 'Send this to someone who...' triggers the #1 viral signal — DM shares.
- *What to change:* Add this exact verbal CTA at the end before you post: 'Comment karo - tee wala ya suit wala?' plus matching caption line.
- *How to do it:* Step 1 — After your final dance at 19.68s-21.18s, record a 1.5s voiceover tag in your editing app saying 'Comment karo - tee wala ya suit wala?' Step 2 — Place it right after the payoff so it doesn't cover your dancing. Step 3 — In your post caption before you post, paste the same line + 'Send this to someone who loves desi transitions ❤️'.
- *What to expect:* More viewers will comment to vote and more will DM-share it to a friend who loves outfit changes because people have a reason to send it.

---

## After judging: time and cost per run

All of this build's runs were made through the website's own API flow (register, upload, poll the real progress, ARIA, share link) on a local stack, with `ANTHROPIC_API_KEY` unset. Cost is the real token cost recorded by the pipeline (Spark prices) plus Modal GPU seconds (approximate) for the extra evidence.

| Run | Wall time | Real cost |
|---|---|---|
| This build, Video-57419, plain swap (Phase 1: original pipeline on Spark only) | 118 s | $0.115 |
| This build, Video-57419, with all extra evidence (cold Modal containers) | 273 s | $0.304 |
| This build, Video-86506, with all extra evidence (warm containers) | 175 s | $0.236 |
| Older studio engine, Video-57419 (its own run report; perception + analysis) | 149 s | $0.331 |
| Older studio engine, Video-86506 (its own run report) | 105 s | $0.239 |

Where the time goes in this build: the two Muse Spark 1.3 scoring calls (about 80-115 s in parallel, mostly hidden reasoning tokens), then the Modal signal models (about 2-3 minutes on a cold start, under a minute warm; they run in parallel with the frame analysis and the speech steps).

## Automatic voice checks on this build's output (20 parameters)

| Check | Video-57419 | Video-86506 |
|---|---|---|
| Problem is one full sentence (8+ words) | 10/10 | 10/10 |
| Quoted dialogue from the video appears in the text | 10/10 | 9/10 |
| 2 or more numbered steps in "How to do it" | 8/10 | 6/10 |
| "How to do it" starts with `Step 1 —` | 9/10 | 9/10 |
| Stray `(at 12.3s)` suffix | 0 | 0 |
| Evidence ids (`E12`) or internal edit names (`zoom_crop`) | 0 | 0 |
| Broken emoji / replacement characters | 0 | 0 |
| Credits, plans, upgrade or pricing words in the result | 0 | 0 |

## Where Muse Spark deviates from what the baseline prompt asks for

The prompt text is the original and was not changed. Observed on these 20 parameters:

- **Single-step fixes.** The prompt asks for "step-by-step" guidance; 6 of 20 parameters came back with one step (mostly advisory parameters such as On-Screen Text and Replay Value), 14 had two or three.
- **Step label style.** The prompt (and the baseline's own `_validate_suggestions`) use `Step N —`; 2 parameters used `Step 1:` with a colon. The text is otherwise in the right voice.
- **Hook "problem" line.** The prompt says the problem field is "the technique they used"; Spark often phrases it as the weakness ("flat opening vocal energy weakens the stop-scroll impact"). Still one full sentence.
- **Overall score.** Not a Spark deviation: the original code multiplies the six scored parameters by niche weights that add up to about 0.6, so averages around 67 produce an overall score around 40 ("Below Average"). The original Desktop code does the same; it was left unchanged on purpose. Worth a look before showing scores to judges.
- **Latency.** Spark reasons before answering, so the two scoring calls take 80-115 s and the first ARIA word appears after about 5 s (chat runs with low reasoning effort).

## Not available during these runs

- Gemini (audio listener and live trends): the API key available during the build answered HTTP 402 "prepayment credits are depleted", so both steps failed soft. Muse Spark 1.2 filled the audio listener keys and the trend block stayed empty. Top up the Gemini key to see the full original behaviour.
- RunPod engagement and the local embedding / aesthetics models (no keys, no torch): the baseline's own fallbacks ran (heuristic engagement; the Modal signal models supply the embeddings).
