// A tiny element builder for the intake screens (no framework, no innerHTML: text goes in as text).

/**
 * h("div", {class: "a b", title: "x", onclick: fn, dataset: {k: 1}, hidden: true}, child, "text", [children])
 * Children may be nodes, strings, numbers, arrays, or null/false (skipped).
 */
export function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k in el && k !== "list") el[k] = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  append(el, kids);
  return el;
}

function append(el, kids) {
  for (const kid of kids) {
    if (kid === null || kid === undefined || kid === false) continue;
    if (Array.isArray(kid)) append(el, kid);
    else el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

/** Replace the children of `el` with `kids`, keeping scroll positions of the elements marked data-keep-scroll. */
export function fill(el, ...kids) {
  const scrolls = [...el.querySelectorAll("[data-keep-scroll]")].map((n) => [n.dataset.keepScroll, n.scrollTop]);
  clear(el);
  append(el, kids);
  for (const [key, top] of scrolls) {
    const n = el.querySelector(`[data-keep-scroll="${key}"]`);
    if (n) n.scrollTop = top;
  }
  return el;
}
