/* Clinic app – no inline scripts (strict CSP). Everything is wired through data-* attributes. */
(function () {
  "use strict";
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };

  // ---- "Show" toggle on every password box (re-hides itself after 20 s)
  $$("input[type=password]").forEach(function (inp) {
    var wrap = document.createElement("div"), b = document.createElement("button"), timer;
    wrap.className = "pw-wrap";
    inp.parentNode.insertBefore(wrap, inp); wrap.appendChild(inp);
    b.type = "button"; b.className = "pw-toggle"; b.textContent = "Show";
    b.setAttribute("aria-label", "Show password"); b.setAttribute("aria-pressed", "false");
    var hide = function () { inp.type = "password"; b.textContent = "Show"; b.setAttribute("aria-pressed", "false"); clearTimeout(timer); };
    b.addEventListener("click", function () {
      if (inp.type === "password") {
        inp.type = "text"; b.textContent = "Hide"; b.setAttribute("aria-pressed", "true");
        timer = setTimeout(hide, 20000);
      } else { hide(); }
    });
    inp.form && inp.form.addEventListener("submit", hide);
    wrap.appendChild(b);
  });

  // ---- print buttons / auto-print
  $$("[data-print]").forEach(function (b) { b.addEventListener("click", function () { window.print(); }); });
  if (document.body.dataset.autoprint === "1") { setTimeout(function () { window.print(); }, 350); }

  // ---- confirm before destructive / irreversible actions
  $$("form[data-confirm]").forEach(function (f) {
    f.addEventListener("submit", function (e) { if (!window.confirm(f.dataset.confirm)) e.preventDefault(); });
  });

  // ---- auto-dismiss success messages
  setTimeout(function () { $$(".alert.success").forEach(function (a) { a.classList.add("hidden"); }); }, 5000);

  // ---- idle logout (server enforces the same timeout). The last *human* activity time lives in
  // sessionStorage so that automatic page refreshes (pharmacy queue) never count as activity.
  var idle = parseInt(document.body.dataset.idle || "0", 10), lf = $("#logout-form");
  if (idle > 0 && lf) {
    var KEY = "clinic_last_active", now = function () { return Date.now(); }, t;
    var last = parseInt(sessionStorage.getItem(KEY) || "0", 10) || now();
    var check = function () {
      var left = idle * 1000 - (now() - last);
      if (left <= 0) { sessionStorage.removeItem(KEY); lf.submit(); return; }
      clearTimeout(t); t = setTimeout(check, left + 50);
    };
    var touch = function () { last = now(); try { sessionStorage.setItem(KEY, String(last)); } catch (e) {} };
    ["click", "keydown", "mousemove", "touchstart", "scroll"].forEach(function (ev) {
      document.addEventListener(ev, touch, { passive: true });
    });
    check();
  }

  // ---- pharmacy queue: refresh when idle so new prescriptions appear by themselves
  var ar = parseInt(document.body.dataset.autoreload || "0", 10);
  if (ar > 0) {
    setInterval(function () {
      var a = document.activeElement;
      if (a && /INPUT|TEXTAREA|SELECT/.test(a.tagName)) return;
      window.location.reload();
    }, ar * 1000);
  }

  // ---- visit form helpers
  var vf = $("#visit-form");
  if (vf) {
    var up = function (s) { return String(s || "").trim().toUpperCase(); };
    var addLine = function (id, text) {
      var ta = $("#" + id); if (!text) return;
      ta.value = (ta.value.trim() ? ta.value.replace(/\s+$/, "") + "\n" : "") + text;
    };
    var calc = function () {
      var n = function (s) { return parseFloat(($("#" + s) || {}).value) || 0; };
      $("#total-view").value = Math.max(0, n("id_consultation_fee") + n("id_medicine_fee") - n("id_discount")).toFixed(2);
    };
    var on = function (id, fn) { var e = $("#" + id); if (e) e.addEventListener("click", fn); };
    on("add-sym", function () { var i = $("#pick-sym"); addLine("id_symptoms", up(i.value)); i.value = ""; });
    on("add-inv", function () { var i = $("#pick-inv"); addLine("id_investigations", up(i.value)); i.value = ""; });
    on("add-med", function () {
      var m = $("#pick-med"), tm = $("#pick-time"), cd = $("#pick-dose"), name = up(m.value);
      if (!name) return;
      var dose = up(cd.value) || tm.value;  // a typed custom dose wins over the standard list
      var k = $("#id_treatment").value.split("\n").filter(function (x) { return x.trim(); }).length + 1;
      addLine("id_treatment", k + "." + name + (dose ? " /" + dose : ""));
      m.value = ""; tm.value = ""; cd.value = "";
    });
    ["id_consultation_fee", "id_medicine_fee", "id_discount"].forEach(function (id) {
      var e = $("#" + id); if (e) e.addEventListener("input", calc);
    });
    $$("[data-dp]").forEach(function (b) {
      b.addEventListener("click", function () {
        var n = function (s) { return parseFloat($("#" + s).value) || 0; };
        var sum = n("id_consultation_fee") + n("id_medicine_fee"), p = parseInt(b.dataset.dp, 10);
        $("#id_discount").value = p === 0 ? "" : Math.round(sum * p / 100);
        calc();
      });
    });
    $$("[data-copy-rx]").forEach(function (b) {  // repeat a past prescription into this visit
      b.addEventListener("click", function () {
        var src = $("#" + b.dataset.copyRx); if (!src) return;
        src.textContent.split("\n").forEach(function (l) {
          l = l.trim().replace(/^\d+\s*[.)]\s*/, ""); if (!l || l === "—") return;
          var k = $("#id_treatment").value.split("\n").filter(function (x) { return x.trim(); }).length + 1;
          addLine("id_treatment", k + "." + l);
        });
        b.textContent = "Copied ✓";
      });
    });
    calc();
  }

  // ---- chronic case form: expand / collapse all sections
  $$("[data-details]").forEach(function (b) {
    b.addEventListener("click", function () {
      $$("details.cc").forEach(function (d) { d.open = b.dataset.details === "open"; });
    });
  });

  // ---- bill form: catalogue autofill + live totals
  var bf = $("#bill-form");
  if (bf) {
    var prices = {}; var raw = $("#catalogue-data");
    if (raw) { try { JSON.parse(raw.textContent).forEach(function (i) { prices[i.name.toLowerCase()] = i.price; }); } catch (e) {} }
    var tbody = $("#bill-rows"), total = $("#id_items-TOTAL_FORMS");
    var recalc = function () {
      var sub = 0;
      $$("tr.bill-row", tbody).forEach(function (tr) {
        var q = parseFloat($("[name$=-qty]", tr).value) || 0, p = parseFloat($("[name$=-unit_price]", tr).value) || 0;
        var amt = q * p; sub += amt; $(".amt", tr).textContent = amt.toFixed(2);
      });
      var d = parseFloat($("#id_discount").value) || 0;
      $("#bill-sub").textContent = sub.toFixed(2); $("#bill-grand").textContent = Math.max(0, sub - d).toFixed(2);
    };
    bf.addEventListener("input", function (e) {
      if (e.target.name && /-name$/.test(e.target.name)) {
        var p = prices[e.target.value.trim().toLowerCase()];
        if (p !== undefined) { var tr = e.target.closest("tr"), pe = $("[name$=-unit_price]", tr); pe.value = p; if (!$("[name$=-qty]", tr).value) $("[name$=-qty]", tr).value = 1; }
      }
      recalc();
    });
    var addRow = $("#add-row");
    if (addRow) addRow.addEventListener("click", function () {
      var tpl = $("#row-template").innerHTML.replace(/__prefix__/g, total.value);
      tbody.insertAdjacentHTML("beforeend", tpl); total.value = parseInt(total.value, 10) + 1;
    });
    recalc();
  }
})();
document.addEventListener("click", function (e) {
  if (e.target.closest("[data-close]")) { window.close(); if (!window.closed) history.length > 1 ? history.back() : (location.href = "/"); }
});
