// The domains' icons, defined here once: the top row's domain chips, ④'s tile headings and any
// other screen (A1's cards, the tool shelf's headings) take them from domainIconSvg. Line icons
// 20 × 20 drawn in currentColor (the tabs' style, js/tabs.js), as high as the letters.

import { DOMAINS } from "./labels.js";

export const DOMAIN_ICON = {
  // a steering wheel
  handling: `<circle cx="10" cy="10" r="7"/><circle cx="10" cy="10" r="1.8"/><path d="M3.2 9.2 8.2 9.6M11.8 9.6 16.8 9.2M10 11.8V17"/>`,
  // a gear
  powertrain: `<circle cx="10" cy="10" r="2.6"/><path d="M10 2.5V5M10 15V17.5M2.5 10H5M15 10H17.5M4.7 4.7 6.5 6.5M13.5 13.5 15.3 15.3M4.7 15.3 6.5 13.5M13.5 6.5 15.3 4.7"/><circle cx="10" cy="10" r="5"/>`,
  // the wind's lines
  aero: `<path d="M2.5 6.5H12A2.3 2.3 0 1 0 9.7 4.2"/><path d="M2.5 10H15.5A2.3 2.3 0 1 1 13.2 12.3"/><path d="M2.5 13.5H9"/>`,
  // a block hitting a wall
  crash: `<path d="M16.5 3V17"/><rect x="3" y="7.5" width="8" height="5" rx="0.8"/><path d="M12.8 6 14.6 4.4M13.2 10H15M12.8 14 14.6 15.6"/>`,
  // a beam bent under a load (the arrow down)
  strength: `<path d="M2.5 13C6 15 14 15 17.5 13"/><path d="M2.5 13V16M17.5 13V16"/><path d="M10 3V10M7.8 7.8 10 10 12.2 7.8"/>`,
  // a speaker with sound
  nvh: `<path d="M3 8H6L10 4.5V15.5L6 12H3Z"/><path d="M13 7.5A3.5 3.5 0 0 1 13 12.5"/><path d="M15.4 5.2A6.8 6.8 0 0 1 15.4 14.8"/>`,
  // a thermometer
  thermal: `<path d="M8.2 11.6V4.3A1.8 1.8 0 0 1 11.8 4.3V11.6A3.4 3.4 0 1 1 8.2 11.6Z"/><path d="M10 7.5V13.5"/>`,
  // a nut and bolt
  assembly: `<path d="M10 2.8 15.6 6V12.4L10 15.6 4.4 12.4V6Z" transform="translate(0 0.8)"/><circle cx="10" cy="10" r="2.4"/>`,
};

// <svg> of a domain's icon ("" for a name that is not a domain); cls is the svg's class.
export function domainIconSvg(domain, cls = "dom-ic") {
  const body = DOMAIN_ICON[domain];
  if (!body) return "";
  return `<svg class="${cls}" data-domain-icon="${domain}" viewBox="0 0 20 20" width="1em" height="1em" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
}

// every domain has one (a check for the tests)
export const ALL_DOMAINS_HAVE_ICONS = DOMAINS.every((d) => !!DOMAIN_ICON[d]);
