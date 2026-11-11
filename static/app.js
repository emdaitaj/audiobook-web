/* Audiobook front end: submit a document, watch it convert, play the result. */
'use strict';

const $ = (id) => document.getElementById(id);
const SEEKS = [
  { s: 5,   label: '5s'  },
  { s: 10,  label: '10s' },
  { s: 30,  label: '30s' },
  { s: 60,  label: '1m'  },
  { s: 120, label: '2m'  },
  { s: 600, label: '10m' },
];

let chosenFile = null;
let chosenRef = null;
let currentJob = null;
let pollTimer = null;
let scrubbing = false;
let opts = null;           // whatever /api/voices told us
let ttsEngine = 'kokoro';
let cloning = false;

/* ------------------------------------------------------------------ utils */
function fmtTime(sec) {
  if (!isFinite(sec) || sec < 0) sec = 0;
  sec = Math.floor(sec);
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  const mm = h ? String(m).padStart(2, '0') : String(m);
  return (h ? h + ':' : '') + mm + ':' + String(s).padStart(2, '0');
}

function say(text, kind) {
  const el = $('msg');
  el.textContent = text || '';
  el.className = 'msg' + (kind ? ' ' + kind : '');
}

/* ------------------------------------------------------------------- tabs */
function showTab(which) {
  const isFile = which === 'file';
  $('tabFile').classList.toggle('on', isFile);
  $('tabText').classList.toggle('on', !isFile);
  $('paneFile').hidden = !isFile;
  $('paneText').hidden = isFile;
}
$('tabFile').onclick = () => showTab('file');
$('tabText').onclick = () => showTab('text');

/* ------------------------------------------------------------- file input */
const drop = $('drop');
drop.onclick = () => $('file').click();
$('file').onchange = (e) => pickFile(e.target.files[0]);

['dragenter', 'dragover'].forEach((ev) =>
  drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add('hot'); }));
['dragleave', 'drop'].forEach((ev) =>
  drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove('hot'); }));
drop.addEventListener('drop', (e) => {
  if (e.dataTransfer.files.length) pickFile(e.dataTransfer.files[0]);
});

function showChosen(box, f, hint) {
  box.classList.add('chosen');
  box.innerHTML = '<b></b><small></small>';
  box.querySelector('b').textContent = f.name;
  box.querySelector('small').textContent =
    (f.size / 1048576).toFixed(1) + ' MB — ' + hint;
}

function pickFile(f) {
  if (!f) return;
  chosenFile = f;
  showChosen(drop, f, 'tap to change');
  say('');
}

/* ------------------------------------------------------- voice engine UI */
const refDrop = $('refDrop');
refDrop.onclick = () => $('refAudio').click();
$('refAudio').onchange = (e) => {
  const f = e.target.files[0];
  if (!f) return;
  chosenRef = f;
  showChosen(refDrop, f, 'tap to change');
  say('');
};

function setEngine(id) {
  ttsEngine = id;
  [...document.querySelectorAll('#engines button')].forEach((b) =>
    b.classList.toggle('on', b.dataset.engine === id));
  $('paneKokoro').hidden = id !== 'kokoro';
  $('paneOmni').hidden = id !== 'omnivoice';
  $('wpmField').hidden = id !== 'omnivoice';
  const found = (opts.engines || []).find((e) => e.id === id);
  $('engineNote').textContent = found ? found.note : '';
  localStorage.setItem('engine', id);
}

function setCloning(on) {
  cloning = on;
  $('tabDesign').classList.toggle('on', !on);
  $('tabClone').classList.toggle('on', on);
  $('paneDesign').hidden = on;
  $('paneClone').hidden = !on;
}

/* One <select> per attribute category, each with a "no preference" option:
   OmniVoice takes at most one value per category and none are required. */
function buildAttrs() {
  const box = $('attrs');
  box.innerHTML = '';
  const chosen = (localStorage.getItem('omniVoice') || opts.omni_default)
    .split(',').map((s) => s.trim());
  opts.omni_attrs.forEach((group) => {
    const row = document.createElement('div');
    row.className = 'attr';
    const label = document.createElement('label');
    label.textContent = group.category;
    const sel = document.createElement('select');
    sel.dataset.category = group.category;
    const none = document.createElement('option');
    none.value = '';
    none.textContent = '—';
    sel.appendChild(none);
    group.values.forEach((v) => {
      const opt = document.createElement('option');
      opt.value = v;
      opt.textContent = v;
      if (chosen.includes(v)) opt.selected = true;
      sel.appendChild(opt);
    });
    sel.onchange = paintVoice;
    row.append(label, sel);
    box.appendChild(row);
  });
  paintVoice();
}

function voiceString() {
  return [...document.querySelectorAll('#attrs select')]
    .map((s) => s.value).filter(Boolean).join(', ');
}

function paintVoice() {
  const v = voiceString();
  $('voicePreview').textContent = v ? '“' + v + '”' : 'pick at least one attribute';
  if (v) localStorage.setItem('omniVoice', v);
}

/* --------------------------------------------------------------- convert */
$('go').onclick = async () => {
  const body = new FormData();
  const onFileTab = !$('paneFile').hidden;
  if (onFileTab) {
    if (!chosenFile) { say('choose a file first', 'bad'); return; }
    body.append('file', chosenFile, chosenFile.name);
  } else {
    const text = $('text').value;
    if (text.trim().split(/\s+/).length < 20) {
      say('paste at least 20 words', 'bad');
      return;
    }
    body.append('text', text);
    body.append('title', $('title').value);
    body.append('fmt', $('fmt').value);
  }
  body.append('speed', $('speed').value);
  body.append('tts_engine', ttsEngine);
  if (ttsEngine === 'kokoro') {
    body.append('voice', $('voice').value);
  } else {
    body.append('lang', $('lang').value);
    body.append('wpm', $('wpm').value);
    if (cloning) {
      if (!chosenRef) { say('choose a voice clip to clone', 'bad'); return; }
      body.append('ref_audio', chosenRef, chosenRef.name);
      body.append('ref_text', $('refText').value);
    } else {
      const v = voiceString();
      if (!v) { say('pick at least one voice attribute', 'bad'); return; }
      body.append('voice', v);
    }
  }

  $('go').disabled = true;
  say('uploading…');
  try {
    const res = await fetch('/api/convert', { method: 'POST', body });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'upload failed');
    say('queued — narration takes roughly a minute per 10 minutes of audio', 'good');
    if (!onFileTab) $('text').value = '';
    refresh(true);
  } catch (err) {
    say(err.message, 'bad');
  } finally {
    $('go').disabled = false;
  }
};

/* ------------------------------------------------------------------ jobs */
async function refresh(fast) {
  let data;
  try {
    const res = await fetch('/api/jobs');
    if (res.status === 401) { location.href = '/login'; return; }
    data = await res.json();
  } catch (err) {
    schedule(5000);
    return;
  }
  renderJobs(data.jobs || []);
  const busy = (data.jobs || []).some((j) =>
    j.state === 'queued' || j.state === 'reading' || j.state === 'narrating');
  schedule(busy || fast ? 2000 : 15000);
}

function schedule(ms) {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(refresh, ms);
}

function renderJobs(jobs) {
  const box = $('jobs');
  if (!jobs.length) {
    box.innerHTML = '<div class="sub">nothing yet</div>';
    return;
  }
  box.innerHTML = '';
  jobs.forEach((j) => box.appendChild(jobCard(j)));
}

function jobCard(j) {
  const el = document.createElement('div');
  el.className = 'job';

  const top = document.createElement('div');
  top.className = 'top';
  const name = document.createElement('div');
  name.className = 'name';
  name.textContent = j.label;
  const state = document.createElement('div');
  state.className = 'state ' + j.state;
  state.textContent = j.state === 'queued' && j.queue_position > 1
    ? '#' + j.queue_position + ' in queue'
    : j.state;
  top.append(name, state);
  el.appendChild(top);

  const detail = document.createElement('div');
  detail.className = 'detail';
  let line = j.error || j.detail || '';
  if (j.state === 'narrating') {
    if (j.stream_seconds) line = fmtTime(j.stream_seconds) + ' narrated so far';
    if (j.eta_minutes) line += ' · about ' + j.eta_minutes + ' min left';
    else if (j.estimate_minutes) line += ' · of about ' + j.estimate_minutes + ' min';
  }
  if (j.state === 'done' && j.words) {
    // Anything under a minute reports as zero, which reads as broken.
    const length = j.audio_minutes ? j.audio_minutes + ' min of audio'
                                   : 'under a minute';
    line = length + ' · ' + j.words.toLocaleString() + ' words';
    if (j.tts_engine === 'omnivoice') {
      line += ' · OmniVoice' + (j.cloned ? ', cloned' : '') +
              (j.lang && j.lang !== 'en' ? ' · ' + j.lang : '');
    }
  }
  detail.textContent = line;
  el.appendChild(detail);

  if (j.state === 'reading' || j.state === 'narrating' || j.state === 'queued') {
    const bar = document.createElement('div');
    bar.className = 'bar';
    const fill = document.createElement('i');
    // The converter only reports a percentage every 200 paragraphs, so a
    // short document never reports one at all. Sliding stripes beat a bar
    // that sits at zero and looks stuck.
    if (j.percent > 0) fill.style.width = j.percent + '%';
    else bar.classList.add('working');
    bar.appendChild(fill);
    el.appendChild(bar);
  }

  const mini = document.createElement('div');
  mini.className = 'mini';
  if (j.has_audio) {
    const play = document.createElement('button');
    play.textContent = 'Play';
    play.onclick = () => openPlayer(j);
    const dl = document.createElement('a');
    dl.textContent = 'Download';
    dl.href = '/audio/' + j.id + '/download';
    mini.append(play, dl);
  } else if (j.can_stream) {
    // Still narrating, but there is already something to listen to.
    const play = document.createElement('button');
    play.textContent = 'Listen now';
    play.onclick = () => openPlayer(j);
    mini.append(play);
  }
  if (j.state === 'queued' || j.state === 'reading' || j.state === 'narrating') {
    const stop = document.createElement('button');
    stop.textContent = 'Cancel';
    stop.className = 'danger';
    stop.onclick = async () => {
      await fetch('/api/job/' + j.id + '/cancel', { method: 'POST' });
      refresh(true);
    };
    mini.appendChild(stop);
  } else {
    const del = document.createElement('button');
    del.textContent = 'Delete';
    del.className = 'danger';
    del.onclick = async () => {
      if (currentJob && currentJob.id === j.id) closePlayer();
      await fetch('/api/job/' + j.id, { method: 'DELETE' });
      refresh(true);
    };
    mini.appendChild(del);
  }
  if (mini.children.length) el.appendChild(mini);
  return el;
}

/* ---------------------------------------------------------------- player */
/* Two ways of playing the same thing.
 *
 * A finished book is an ordinary file: the browser gets its length, knows the
 * duration, and seeks with range requests.
 *
 * A book still being narrated has no length yet, so none of that works. The
 * converter encodes constant bit rate MP3, though, which means a position in
 * seconds is exactly a byte offset - so we ask the server to start the stream
 * at that offset and keep our own clock. Seeking is a re-request; pausing is
 * just pausing. From the outside the two behave the same. */
const audio = $('audio');

const live = {
  on: false,        // are we playing a file that is still being written?
  base: 0,          // seconds of audio that came before this stream started
  info: null,       // data_start / bytes_per_second / frame_bytes
  available: 0,     // seconds that exist so far
  total: 0,         // best guess at the finished length
  complete: false,
  timer: null,
};

function buildSeekRows() {
  const back = $('seekBack');
  const fwd = $('seekFwd');
  back.innerHTML = '';
  fwd.innerHTML = '';
  SEEKS.slice().reverse().forEach((step) => {
    const b = document.createElement('button');
    b.textContent = '−' + step.label;
    b.onclick = () => nudge(-step.s);
    back.appendChild(b);
  });
  SEEKS.forEach((step) => {
    const b = document.createElement('button');
    b.textContent = '+' + step.label;
    b.onclick = () => nudge(step.s);
    fwd.appendChild(b);
  });
}

/* ---- one clock, whichever mode we are in ---- */
function nowAt() {
  return live.on ? live.base + audio.currentTime : audio.currentTime;
}

function totalLen() {
  if (live.on) return Math.max(live.total, live.available);
  return isFinite(audio.duration) ? audio.duration : 0;
}

/* How far you are allowed to seek. Past what has been narrated there is
   simply nothing to play yet. */
function seekLimit() {
  return live.on ? live.available : totalLen();
}

function seekTo(seconds) {
  const limit = seekLimit();
  const target = Math.min(Math.max(0, seconds), Math.max(0, limit - 0.3));
  if (!live.on) {
    audio.currentTime = target;
    paint();
    return;
  }
  const info = live.info;
  const frames = Math.floor((target * info.bytes_per_second) / info.frame_bytes);
  const byte = info.data_start + frames * info.frame_bytes;
  const wasPlaying = !audio.paused;
  live.base = (byte - info.data_start) / info.bytes_per_second;
  audio.src = '/audio/' + currentJob.id + '/stream?start=' + byte;
  audio.load();
  if (wasPlaying) audio.play().catch(() => {});
  paint();
}

function nudge(delta) {
  if (!audio.src) return;
  seekTo(nowAt() + delta);
}

function posKey(id) { return 'pos:' + id; }

/* ---- opening ---- */
async function openPlayer(job) {
  const fresh = !currentJob || currentJob.id !== job.id;
  if (fresh) {
    reader.loadedFor = null;
    reader.at = -1;
    $('readerBody').innerHTML = '';
  }
  currentJob = job;
  $('player').hidden = false;
  $('pTitle').textContent = job.label;
  $('dl').href = '/audio/' + job.id + '/download';
  $('tx').href = '/transcript/' + job.id;
  $('tx').hidden = !job.has_transcript;
  if (!fresh) return;
  if (reader.open) loadOutline(true);

  stopLivePolling();
  live.on = false;
  live.base = 0;
  const resume = parseFloat(localStorage.getItem(posKey(job.id)) || '0');

  if (job.state === 'done') {
    audio.dataset.job = job.id;
    audio.src = '/audio/' + job.id;
    audio.load();
    if (resume > 5) {
      audio.addEventListener('loadedmetadata', function once() {
        audio.removeEventListener('loadedmetadata', once);
        if (resume < audio.duration - 5) audio.currentTime = resume;
        paint();
      });
    }
  } else {
    const info = await fetchAudioInfo(job.id);
    if (!info || !info.ready || info.available_seconds < 2) {
      $('liveTag').hidden = false;
      $('liveTag').textContent = 'waiting for the first audio…';
      audio.dataset.job = job.id;
      startLivePolling(job.id, resume);
      paint();
      return;
    }
    applyInfo(info);
    audio.dataset.job = job.id;
    live.on = true;
    seekTo(resume > 5 && resume < info.available_seconds - 5 ? resume : 0);
    startLivePolling(job.id);
  }
  mediaSession(job);
  $('player').scrollIntoView({ behavior: 'smooth', block: 'center' });
  audio.play().catch(() => {});
  paint();
}

async function fetchAudioInfo(id) {
  try {
    const res = await fetch('/api/job/' + id + '/audio-info');
    if (!res.ok) return null;
    return await res.json();
  } catch (err) {
    return null;
  }
}

function applyInfo(info) {
  live.info = {
    data_start: info.data_start,
    frame_bytes: info.frame_bytes,
    bytes_per_second: info.bytes_per_second,
  };
  live.available = info.available_seconds;
  live.complete = info.complete;
  live.total = info.complete ? info.available_seconds
                             : Math.max(info.estimate_seconds || 0,
                                        info.available_seconds);
}

/* While a book is still being narrated, keep asking how much of it exists. */
function startLivePolling(id, pendingResume) {
  stopLivePolling();
  live.timer = setInterval(async () => {
    if (!currentJob || currentJob.id !== id) { stopLivePolling(); return; }
    const info = await fetchAudioInfo(id);
    if (!info || !info.ready) return;
    applyInfo(info);

    // Nothing was playable when the player opened; start now that it is.
    if (!live.on && info.available_seconds >= 2) {
      live.on = true;
      $('liveTag').hidden = false;
      seekTo(pendingResume > 5 && pendingResume < info.available_seconds - 5
             ? pendingResume : 0);
      audio.play().catch(() => {});
    }
    if (live.complete) {
      stopLivePolling();
      $('liveTag').hidden = true;
      // The estimate was against a guessed length; now there is a real one.
      if (reader.open) loadOutline(true);
    }
    paint();
  }, 3000);
}

function stopLivePolling() {
  if (live.timer) clearInterval(live.timer);
  live.timer = null;
}

function closePlayer() {
  stopLivePolling();
  audio.pause();
  audio.removeAttribute('src');
  audio.dataset.job = '';
  audio.load();
  live.on = false;
  currentJob = null;
  reader.loadedFor = null;
  reader.at = -1;
  $('player').hidden = true;
}
$('closePlayer').onclick = closePlayer;

$('play').onclick = () => {
  if (audio.paused) audio.play().catch(() => {}); else audio.pause();
};

/* ---- drawing ---- */
function paint() {
  const at = nowAt();
  const total = totalLen();
  $('tCur').textContent = fmtTime(at);
  $('tDur').textContent = total
    ? (live.on && !live.complete ? '~' : '') + fmtTime(total)
    : '—:—';
  if (!scrubbing) {
    $('scrub').value = total ? Math.round((at / total) * 1000) : 0;
  }
  $('play').innerHTML = audio.paused ? '&#9654;' : '&#10074;&#10074;';
  if (live.on && !live.complete) {
    $('liveTag').hidden = false;
    $('liveTag').textContent = 'narrating as you listen · ' +
      fmtTime(live.available) + ' ready';
  } else if (!live.timer) {
    $('liveTag').hidden = true;
  }
}

audio.addEventListener('timeupdate', () => {
  paint();
  markPosition();
  if (currentJob && nowAt() > 3) {
    localStorage.setItem(posKey(currentJob.id), String(nowAt()));
  }
});
['loadedmetadata', 'durationchange', 'play', 'pause', 'seeked']
  .forEach((ev) => audio.addEventListener(ev, paint));
audio.addEventListener('seeked', () => markPosition(true));

/* The stream ends whenever the encoder has not written the next second yet.
   That is not the end of the book, so pick it up again where it stopped. */
audio.addEventListener('ended', () => {
  paint();
  if (!live.on || live.complete) return;
  const at = nowAt();
  if (at < live.available - 1) seekTo(at);
});

audio.addEventListener('error', () => {
  if (!audio.src) return;
  if (live.on && !live.complete) return;   // a dropped stream, not a real fault
  say('could not load that audio', 'bad');
});

const scrub = $('scrub');
const startScrub = () => { scrubbing = true; };
const endScrub = () => {
  if (!scrubbing) return;
  scrubbing = false;
  const total = totalLen();
  if (total) seekTo((scrub.value / 1000) * total);
};
scrub.addEventListener('input', () => {
  scrubbing = true;
  $('tCur').textContent = fmtTime((scrub.value / 1000) * totalLen());
});
['pointerdown', 'touchstart', 'mousedown'].forEach((ev) =>
  scrub.addEventListener(ev, startScrub));
['change', 'pointerup', 'touchend', 'mouseup'].forEach((ev) =>
  scrub.addEventListener(ev, endScrub));

/* ---- the text, lined up against the audio ----
 * Tap a sentence and the audio jumps there. Timings come from the server:
 * exact if the converter ever writes them, otherwise spread across the known
 * duration by word count, which measures out about a second off. */
const reader = {
  open: false,
  follow: true,      // keep the playing sentence in view
  paras: [],         // [{start, end, sentences:[{start, text}]}]
  flat: [],          // every sentence, in order, for the current-position hunt
  at: -1,            // which sentence is highlighted
  exact: false,
  loadedFor: null,
  scrolledAt: 0,     // when the reader was last scrolled by hand
};

$('toggleText').onclick = async () => {
  reader.open = !reader.open;
  $('reader').hidden = !reader.open;
  $('toggleText').textContent = reader.open ? 'Hide text' : 'Show text';
  if (reader.open) await loadOutline();
};

$('followBtn').onclick = () => {
  reader.follow = !reader.follow;
  $('followBtn').classList.toggle('on', reader.follow);
};

$('readerBody').addEventListener('scroll', () => {
  reader.scrolledAt = Date.now();
});

async function loadOutline(force) {
  if (!currentJob) return;
  if (!force && reader.loadedFor === currentJob.id) return;
  const body = $('readerBody');
  body.textContent = 'loading the text…';
  let data;
  try {
    data = await (await fetch('/api/job/' + currentJob.id + '/outline')).json();
  } catch (err) {
    body.textContent = 'could not load the text';
    return;
  }
  if (!data.ready) {
    body.textContent = data.reason || 'no text yet';
    reader.loadedFor = null;
    return;
  }
  reader.paras = data.paragraphs;
  reader.exact = data.exact;
  reader.loadedFor = currentJob.id;
  reader.at = -1;
  reader.flat = [];
  data.paragraphs.forEach((p) => p.sentences.forEach((s, k) =>
    reader.flat.push({ start: s.start, para: p.i, k })));

  $('readerNote').textContent = data.exact
    ? 'tap any sentence to jump there'
    : 'tap any sentence to jump there · timings estimated, within a second or so'
      + (data.complete ? '' : ' · still narrating');
  renderOutline();
  markPosition(true);
}

function renderOutline() {
  const body = $('readerBody');
  body.innerHTML = '';
  reader.paras.forEach((p) => {
    const el = document.createElement('p');
    el.className = 'para';
    el.dataset.i = p.i;
    p.sentences.forEach((s, k) => {
      const span = document.createElement('span');
      span.className = 'sent';
      span.dataset.para = p.i;
      span.dataset.k = k;
      if (k === 0) {
        const stamp = document.createElement('span');
        stamp.className = 'stamp';
        stamp.textContent = fmtTime(p.start);
        span.appendChild(stamp);
      }
      span.appendChild(document.createTextNode(s.text + ' '));
      span.onclick = () => {
        seekTo(s.start);
        if (audio.paused) audio.play().catch(() => {});
      };
      el.appendChild(span);
    });
    body.appendChild(el);
  });
}

/* Which sentence is being read right now. */
function markPosition(jump) {
  if (!reader.open || !reader.flat.length) return;
  const at = nowAt();
  let lo = 0, hi = reader.flat.length - 1, found = 0;
  while (lo <= hi) {                       // last sentence that starts at or before now
    const mid = (lo + hi) >> 1;
    if (reader.flat[mid].start <= at) { found = mid; lo = mid + 1; } else hi = mid - 1;
  }
  if (found === reader.at) return;
  reader.at = found;

  const body = $('readerBody');
  body.querySelectorAll('.sent.at').forEach((el) => el.classList.remove('at'));
  body.querySelectorAll('.para.at').forEach((el) => el.classList.remove('at'));
  const here = reader.flat[found];
  const span = body.querySelector(
    '.sent[data-para="' + here.para + '"][data-k="' + here.k + '"]');
  if (!span) return;
  span.classList.add('at');
  span.closest('.para').classList.add('at');

  // Do not yank the page away from somebody who is reading ahead.
  const busyScrolling = Date.now() - reader.scrolledAt < 4000;
  if (reader.follow && (jump || !busyScrolling)) {
    const top = span.offsetTop - body.clientHeight / 2;
    body.scrollTo({ top: Math.max(0, top), behavior: jump ? 'auto' : 'smooth' });
  }
}

/* ---- playback speed ---- */
const RATE_MIN = 0.5;
const RATE_MAX = 2.5;
const rate = $('rate');

function setRate(value) {
  const clamped = Math.min(Math.max(value, RATE_MIN), RATE_MAX);
  // Float arithmetic on 0.05 steps drifts; two decimals is the real precision.
  const tidy = Math.round(clamped * 100) / 100;
  audio.playbackRate = tidy;
  rate.value = Math.round(tidy * 100);
  $('rateVal').textContent = tidy.toFixed(2) + '×';
  $('rateReset').disabled = Math.abs(tidy - 1) < 0.001;
  localStorage.setItem('rate', String(tidy));
}

rate.addEventListener('input', () => setRate(rate.value / 100));
$('rateDown').onclick = () => setRate(audio.playbackRate - 0.05);
$('rateUp').onclick = () => setRate(audio.playbackRate + 0.05);
$('rateReset').onclick = () => setRate(1);


/* lock-screen / headset controls on a phone */
function mediaSession(job) {
  if (!('mediaSession' in navigator)) return;
  navigator.mediaSession.metadata = new MediaMetadata({
    title: job.label, artist: 'Audiobook', album: 'local TTS',
  });
  const set = (action, fn) => {
    try { navigator.mediaSession.setActionHandler(action, fn); } catch (e) {}
  };
  set('play', () => audio.play());
  set('pause', () => audio.pause());
  set('seekbackward', (d) => nudge(-(d && d.seekOffset ? d.seekOffset : 30)));
  set('seekforward', (d) => nudge(d && d.seekOffset ? d.seekOffset : 30));
  set('seekto', (d) => { if (d && d.seekTime != null) audio.currentTime = d.seekTime; });
}

/* keyboard, for when this is open on a laptop */
document.addEventListener('keydown', (e) => {
  if (/^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) return;
  if ($('player').hidden) return;
  if (e.code === 'Space') { e.preventDefault(); $('play').click(); }
  if (e.code === 'ArrowLeft') nudge(e.shiftKey ? -60 : -10);
  if (e.code === 'ArrowRight') nudge(e.shiftKey ? 60 : 10);
});

/* ------------------------------------------------------------------ boot */
$('signout').onclick = async (e) => {
  e.preventDefault();
  await fetch('/api/logout', { method: 'POST' });
  location.href = '/login';
};

async function boot() {
  buildSeekRows();
  setRate(parseFloat(localStorage.getItem('rate') || '1'));

  try {
    opts = await (await fetch('/api/voices')).json();
    buildVoiceControls();
  } catch (err) {
    say('could not load the voice list; the server defaults will be used', 'bad');
  }

  refresh();
}

function buildVoiceControls() {
  // engine buttons
  const box = $('engines');
  box.innerHTML = '';
  opts.engines.forEach((e) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.dataset.engine = e.id;
    b.textContent = e.name;
    b.disabled = !e.available;
    if (!e.available) b.title = e.name + ' is not installed';
    b.onclick = () => setEngine(e.id);
    box.appendChild(b);
  });

  // Kokoro's fixed cast
  const sel = $('voice');
  sel.innerHTML = '';
  const wantVoice = localStorage.getItem('voice') || opts.default;
  opts.voices.forEach((v) => {
    const opt = document.createElement('option');
    opt.value = v.id;
    opt.textContent = v.name;
    if (v.id === wantVoice) opt.selected = true;
    sel.appendChild(opt);
  });
  sel.onchange = () => localStorage.setItem('voice', sel.value);

  // OmniVoice: attributes, languages, pace
  buildAttrs();
  const lang = $('lang');
  lang.innerHTML = '';
  const wantLang = localStorage.getItem('lang') || opts.default_lang;
  opts.languages.forEach((l) => {
    const opt = document.createElement('option');
    opt.value = l.id;
    opt.textContent = l.name;
    if (l.id === wantLang) opt.selected = true;
    lang.appendChild(opt);
  });
  lang.onchange = () => localStorage.setItem('lang', lang.value);

  const wpm = $('wpm');
  wpm.value = localStorage.getItem('wpm') || opts.default_wpm;
  $('wpmVal').textContent = wpm.value;
  wpm.oninput = () => {
    $('wpmVal').textContent = wpm.value;
    localStorage.setItem('wpm', wpm.value);
  };

  $('tabDesign').onclick = () => setCloning(false);
  $('tabClone').onclick = () => setCloning(true);
  setCloning(false);

  const saved = localStorage.getItem('engine');
  const usable = opts.engines.filter((e) => e.available).map((e) => e.id);
  setEngine(usable.includes(saved) ? saved
            : (usable.includes(opts.default_engine) ? opts.default_engine
               : (usable[0] || 'kokoro')));
}
boot();
