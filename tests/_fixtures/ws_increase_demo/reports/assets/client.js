// server/client.js — feature-detects the live server; if absent, the page is static.
(function () {
  var guidance = document.getElementById("guidance");
  if (!guidance) return;

  function detectAndStart() {
    fetch("/api/state", { method: "GET" })
      .then(function (r) { if (r.ok) return start(); })
      .catch(function () { /* static mode — no live updates */ });
  }

  function start() {
    // Poll guidance content every 2s
    setInterval(function () {
      fetch("/api/guidance").then(function (r) {
        if (r.status === 204) {
          guidance.classList.remove("active");
          guidance.innerHTML = "";
          return;
        }
        if (r.ok) {
          r.text().then(function (t) {
            guidance.innerHTML = t;
            guidance.classList.add("active");
          });
        }
      }).catch(function () { /* server gone, ignore */ });
    }, 2000);

    // SSE for state changes
    try {
      var es = new EventSource("/api/events");
      es.addEventListener("state", function (e) {
        try {
          var data = JSON.parse(e.data);
          window.dispatchEvent(new CustomEvent("pbg:state", { detail: data }));
        } catch (err) { /* ignore */ }
      });
    } catch (err) { /* old browsers */ }

    // Wire click events back to the server (handles option/card patterns)
    document.body.addEventListener("click", function (e) {
      var opt = e.target.closest("[data-choice]");
      if (!opt) return;
      var payload = JSON.stringify({
        choice: opt.dataset.choice,
        text: (opt.innerText || "").trim(),
        timestamp: Math.floor(Date.now() / 1000)
      });
      fetch("/api/click", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: payload
      }).catch(function () { /* offline — drop silently */ });
      opt.classList.add("selected");
    });
  }

  detectAndStart();
})();
