/**
 * Client-side tracking script for the mock survey.
 * Adapted from trackers/general/generalTracker.htm and trackers/keylog/keylog.js.
 * Tracks keystrokes, mouse movement, paste/copy, tab visibility, and time-on-page.
 */
(function () {
  const startTime = Date.now();

  let mouseMoveCount = 0;
  let clickCount = 0;
  let keyCount = 0;
  let pasteDetected = false;
  let copyDetected = false;
  let tabHidden = false;
  let scrollEventCount = 0;
  const eventLog = [];
  const keyLog = [];

  // Mouse tracking
  document.addEventListener("mousemove", function () {
    mouseMoveCount++;
  });

  document.addEventListener("click", function () {
    clickCount++;
  });

  document.addEventListener("scroll", function () {
    scrollEventCount++;
  }, { passive: true });

  // Keystroke logging (with timestamps, matching keylog.js format)
  document.addEventListener("keydown", function (e) {
    keyCount++;
    keyLog.push({ key: e.key, time: Date.now() });
  });

  // Detect large input jumps (paste detection via input size)
  const textareas = document.querySelectorAll("textarea, input[type='text']");
  textareas.forEach(function (field) {
    let lastLen = field.value.length;
    field.addEventListener("input", function () {
      const len = field.value.length;
      const jump = len - lastLen;
      if (jump > 10) {
        keyLog.push({
          key: "INPUT_JUMP",
          time: Date.now(),
          jump: jump,
          total: len
        });
      }
      lastLen = len;
    });
  });

  // Paste detection
  document.addEventListener("paste", function () {
    pasteDetected = true;
    eventLog.push({ event: "PASTE", time: Date.now() });
  });

  // Copy detection
  document.addEventListener("copy", function () {
    copyDetected = true;
    eventLog.push({ event: "COPY", time: Date.now() });
  });

  // Tab visibility
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) {
      tabHidden = true;
      eventLog.push({ event: "TAB_HIDDEN", time: Date.now() });
    } else {
      eventLog.push({ event: "TAB_VISIBLE", time: Date.now() });
    }
  });

  // Window blur/focus
  window.addEventListener("blur", function () {
    eventLog.push({ event: "WINDOW_BLUR", time: Date.now() });
  });

  window.addEventListener("focus", function () {
    eventLog.push({ event: "WINDOW_FOCUS", time: Date.now() });
  });

  // Before form submission, serialize tracking data into the hidden field
  const forms = document.querySelectorAll("form");
  forms.forEach(function (form) {
    form.addEventListener("submit", function () {
      const endTime = Date.now();
      const timeOnPage = Math.round((endTime - startTime) / 1000);

      const trackingData = {
        start_time: startTime,
        time_on_page: timeOnPage,
        mouse_move_count: mouseMoveCount,
        click_count: clickCount,
        total_keys: keyCount,
        paste_detected: pasteDetected,
        copy_detected: copyDetected,
        tab_hidden: tabHidden,
        scroll_event_count: scrollEventCount,
        event_log: eventLog,
        key_log: keyLog,
        ts: endTime
      };

      const hiddenField = form.querySelector('input[name="__tracking"]');
      if (hiddenField) {
        hiddenField.value = JSON.stringify(trackingData);
      }
    });
  });
})();
