# Camera & platform notes

Everything on this page cost real debugging time against a real Nikon
D5500 on a real Mac. If you're adapting this project to different
hardware, this is the page most worth reading first. Several of these
are not documented anywhere in gphoto2's own docs, and a couple of them
directly contradict what seems like the obvious API to use.

## Config node names aren't portable

gphoto2 config node names vary by camera vendor, model, and even driver
version. Two examples that cost real time here:

- **Aperture is `f-number` on Nikon PTP, not `aperture`.** `aperture` is
  the Canon spelling. Asking for a node that doesn't exist raises a bare
  `GPhoto2Error [-2] Bad parameters` that says nothing about *which* name
  was wrong.
- **The image-quality node is `imagequality`, not `imageformat`.**
  `imageformat` doesn't exist at all on the D5500; it's an easy name to
  guess wrong.

`camera.py` has
`list_config_names()` and `find_config_name()`, which walk the camera's
actual config tree and return what's really there.
`resolve_aperture_name()` tries a list of known aliases
(`f-number`, `aperture`, `fnumber`, `aperturevalue`) in order and uses
whichever one the camera actually reports, memoised so it's only looked
up once per session. `verify_required_config_nodes()` checks that the
nodes every bracket depends on exist *at connect time*, so a missing node
fails immediately with a clear message and a list of what's actually
available.

**If you're on a different camera:** run
`eclipse-throughput --list-image-quality` and
`eclipse-throughput --list-capture-target` before assuming any config
name or choice string in this codebase applies to you.

## `capturetarget` defaults to RAM on many cameras

gphoto2's own docs, if you go looking: *"setting this to sdram will make
the camera capture directly into the camera RAM and not on the memory
card. You need to download the image in the same gphoto2 call, otherwise
it will [be] gone when the connection is closed."*

Many cameras default to exactly this. Every bracket plan in this project
assumes captures survive on the card without being downloaded during the
event. That assumption breaks if `capturetarget` is pointed at
RAM, and a long enough burst into a full RAM buffer can hang the camera
outright.

`connect()` forces this to the card-writing choice and **reads it back
to confirm the change actually took**. Some camera/driver combinations
have been reported not to apply it, and we raise loudly rather than
proceeding on an unverified assumption. See `camera._force_capture_target_to_card`.

## Combo RAW and JPEG image quality is unsafe

Unfortunately, capturing combo image quality is unsafe with this project's capture mechanism. **each physical capture in a RAW+JPEG combo mode
fires two separate `FILE_ADDED` events** (one per file), but
`trigger_capture_one()` consumes exactly one event per call and treats
that as confirmation. This means that the bracket reports "confirmed" for a shutter
speed that was never actually photographed.

This only breaks for combo modes, where one capture produces two events.
Plain RAW or plain JPEG produce exactly one event per capture and don't
have this problem. `camera.is_raw_jpeg_combo_quality()` detects it (a `+`
in the choice string), and `eclipse-run` refuses to start with a combo
`image_quality` configured (`SystemExit`), before any waiting begins.

## `wait_for_event()` returns the first event of ANY type

gphoto2's own doc for
`gp_camera_wait_for_event()`: *"If timeout occurs before an event is
received then `*eventtype==GP_EVENT_TIMEOUT`... If an event is received
then eventtype is set to the type of event"*

The D5500 camera fires a constant stream of property-change notifications
around every capture (`expprogram` changed, `continousshootingcount`
changed, `ExposureRemaining` changed. This is visible directly via the
`gphoto2` CLI's own `--wait-event-and-download`). A single
`wait_for_event()` call after a trigger can catch one of these instead
of the real `FILE_ADDED`, and if your code only checks once, it silently
undercounts real captures the camera actually completed. This was the
root cause of an early, very confusing measurement where a throughput
test reported drastically fewer confirmed frames than the camera's LED
activity showed were actually happening.

`trigger_capture_one()` loops, discarding everything except
`FILE_ADDED`, until it either sees one or a timeout elapses. If you're
writing new gphoto2 event-handling code, loop; never check once.

## `capture()` vs `trigger_capture()` — a real, measured speed difference

`gp_camera_capture()` (the plain "take a photo" call) does a full
trigger-wait-resolve cycle internally. In practice (this matches
independent reports for other cameras) resolving that internally
appears to retrieve the object's data as a side effect of completing the
capture, *regardless* of whether your own code ever calls
`file_get()` afterward. That's most of why it's very slow.

`gp_camera_trigger_capture()` is a different, lower-level operation: it
fires the shutter and returns immediately, with confirmation handled
separately via `wait_for_event()`. Measured directly on this camera:
**~0.51 fps via plain `capture()` vs. ~0.82 fps via
`trigger_capture()` + polling**, at the same image quality, once a
one-time per-session cost (below) is out of the way. That's the entire
reason `camera.trigger_capture_one()` exists and is what every bracket
plan actually calls. `capture_one()` survives only for warm-up,
`focus_check`, and `throughput_test`'s own comparison sweep.

**A one-time first-call cost, and how we ruled out the wrong causes.**
The *first* `trigger_capture_one()` call in a freshly-opened session can
take tens of seconds. Two plausible explanations were tested and ruled
out: rebooting the camera didn't change it (so it isn't a camera-side
write-buffer state), and bare `trigger_capture()` from the CLI, with no
event listener at all, was always instant. The remaining
explanation: opening a PTP session appears to emit a burst of initial
"here's my current state" property-change events, and only a caller
actually polling for `FILE_ADDED` has to churn through that backlog. It's
a one-time session cost, not a per-capture one. This was confirmed by warming
up with a single ordinary capture before timing anything, which
consistently eliminated the delay. In this project's schedule, ordinary
captures happen throughout the long partial phase well before anything
time-critical runs, so nothing extra was needed to work around it.

**Per-shot overhead is flat, not proportional to exposure time.** Timed
across the real 8-step bracket (`1/2000` through several full seconds):
overhead stayed in a narrow ~1.1–1.35s band whether the shutter was open
for a fraction of a millisecond or several seconds. This is why
`bracket_overhead` is a single calibrated constant added per shot, not
scaled by exposure length.

## Autofocus can't be prevented, and AF-lock doesn't survive tethering

Two separate findings, worth knowing before assuming you can lean on
autofocus during an automated sequence:

- `focusmode` is **read-only** over PTP on the D5500 camera tier (confirmed
  via an identical limitation reported for a same-generation Nikon body).
  gphoto2 cannot force manual focus remotely.
- Reassigning the AE-L/AF-L button to AF-ON (a real, documented Custom
  Setting on this camera) does **not** decouple the shutter from
  triggering autofocus. It only adds an *additional* way to trigger AF.
  The setting that would fully decouple them (`AF activation: AF-ON
  only`) is a higher-tier-body feature this camera doesn't have.

Net effect: **every scripted capture re-triggers autofocus**, with no
way to prevent it in software. The only reliable fix is mechanical: use
AF (or the reassigned AF-ON button) to *acquire* focus quickly, then
physically flip the lens's own A/M switch to Manual. 

## The LCD goes dark while tethered

The rear LCD (both live view and image review) goes blank the moment the
camera enters PTP remote-control mode. Confirmed this isn't
camera-tier-specific by finding the same complaint for a higher-end body.

Two workarounds, used together here:

- `camera.trigger_capture_one(..., download_dir=...)` (wired through
  `config.yaml`'s `camera.download_phases`) downloads the **full** file
  during the long partial phases and logs a sharpness score where the
  format allows it. This is good enough to catch focus drift or fogging without
  stopping anything. The camera's own embedded preview thumbnail (160×120)
  is not usable for this; a full download is needed even though it costs
  some transfer time.
- The optical viewfinder is unaffected as it's a separate mechanical light
  path (mirror -> pentamirror -> eyepiece), with no dependency on the
  camera's digital LCD electronics at all. It works exactly as normal
  while tethered. For a DSLR, this is the reliable fallback for anything
  the LCD would normally show you.

## The macOS PTP daemon

macOS runs a system PTP daemon (`PTPCamera` on older releases,
`ptpcamerad` on newer ones) that claims a camera the instant it finishes
enumerating over USB. If gphoto2 tries to connect after that, `init()`
fails with `GPhoto2Error [-53] Could not claim the USB device` —
permanently, since the daemon never yields on its own.

`camera.free_macos_usb_claim()` stops it (`killall`, both possible
names); launchd relaunches it on demand, so nothing is permanently
changed. Two important refinements, both learned from real failures
during rehearsal:

- **Killing it and retrying `init()` immediately doesn't work** — freeing
  a USB claim isn't instantaneous, and an immediate retry just lands
  mid-re-detection (`[-105] Unknown model`) instead. `connect()` retries
  with a real delay between attempts, not just once.
- **Don't nest retry loops.** An earlier version had `connect()` retry
  internally *and* an outer reconnect loop retry `connect()`, which
  multiplied into several daemon kills per outer attempt. Killing the
  daemon repeatedly in a tight loop just races `launchd`'s relaunch and
  is actively counterproductive. A rate limit
  (`camera.DAEMON_KILL_COOLDOWN`) now prevents this, and `connect()`
  takes an `attempts` parameter specifically so a caller-level retry loop
  can pass `attempts=1` and own the pacing itself.

**This capability defaults to off** (`camera.auto_free_usb_claim: false`
— see [`config-reference.md`](config-reference.md#camera)). Automatically
killing a system process turned
out, empirically, to be a worse trade than just telling the user to
power-cycle the camera. Use `eclipse-camera-check --fix` for an explicit,
human-initiated version of the same fix.

**If retries alternate between `[-53]` and `[-105]` indefinitely**, the
camera's own USB stack is very likely wedged, not the daemon. No amount
of host-side retrying fixes that. Power-cycle the camera itself (cable
out, camera off, wait ~10s, on, cable in), not just the connection.

## A sleeping laptop is worse than a crashed one

gphoto2 calls have no built-in timeout. If the machine sleeps while one
is in flight, the USB stack can be torn down mid-call and the process
hangs indefinitely, rather than raising an exception the reconnect logic
could catch. Observed directly during a rehearsal: the script froze on
wake and had to be force-killed.

`camera.prevent_system_sleep()` spawns `caffeinate -dimsu -w <pid>` at
startup. This is a separate process holding the sleep assertion, which exits by
itself when the main process does, so nothing is left running after a
crash. Controlled by `config.yaml`'s `prevent_sleep` (on by default).
Worth setting your system energy preferences as a second layer regardless. `caffeinate` doesn't prevent sleep from closing the lid on battery.

## Why nothing downloads during capture

`run_eclipse.py`'s bracket-firing code
never downloads full files during the event (only the optional
`download_phases` monitoring preview does, during the long
partial phases). Even
plain no-download `capture()` calls, on inspection, turned out to already
be paying most of the retrieval cost internally (see above), so "leave
everything on the card" isn't a speed hack layered on top, it's simply
not adding a second, redundant transfer on top of what the capture call
already does. Everything comes off the card afterward with
`scripts/pull_from_card.py`, which shells out to the `gphoto2` CLI's
`--get-all-files` rather than the Python bindings, since its recursive
folder-walking is more reliably version-stable than reimplementing it.

## The D5500's burst mode tops out at 3 frames per trigger

Setting the `burstnumber` PTP property and firing a single trigger does
produce a genuine hardware-paced burst. I confirmed this by ear, and heard multiple real
shutter actuations per trigger. But on this camera it degrades with rapid
consecutive triggers: 3 frames, then 2, then 1, stabilizing back to a
reliable rate only with roughly 2.5–3 seconds of recovery between
trigger groups. That works out to about the same net throughput as the
simpler single-trigger-per-shot design already gives, for meaningfully
more complexity and a real risk of misjudging the recovery interval on
the day. I decided against pursuing it. The single-trigger design in
`trigger_capture_one()` is what was chosen deliberately as the more stable choice.
