import { Component, input } from '@angular/core';

const PATHS: Record<string, string[] | undefined> = {
  logo: ['M12 3 18 7v7l-6 4-6-4V7l6-4Z', 'm6 7 6 4 6-4M12 11v7', 'm3 10 3 2m12 0 3-2M9 20l3 2 3-2'],
  grid: ['M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z'],
  mic: [
    'M12 15a3 3 0 0 0 3-3V5a3 3 0 0 0-6 0v7a3 3 0 0 0 3 3Z',
    'M5 10v2a7 7 0 0 0 14 0v-2M12 19v3m-4 0h8',
  ],
  'mic-off': [
    'm3 3 18 18M9 9v3a3 3 0 0 0 5 2M9 5a3 3 0 0 1 6 0v6',
    'M5 10v2a7 7 0 0 0 12 5m2-5v-2M12 19v3m-4 0h8',
  ],
  transcript: ['M8 4h12M8 9h12M8 14h8M8 19h10M3 4h.01M3 9h.01M3 14h.01M3 19h.01'],
  check: ['m5 12 4 4L19 6'],
  checkSquare: ['M9 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-9', 'm9 10 3 3L22 3'],
  file: [
    'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6Z',
    'M14 2v6h6M8 13h8M8 17h5',
  ],
  folder: ['M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z'],
  arrow: ['M4 12h16m-6-6 6 6-6 6'],
  'arrow-up': ['M12 20V4m-6 6 6-6 6 6'],
  chevron: ['m9 5 7 7-7 7'],
  plus: ['M12 5v14M5 12h14'],
  close: ['m6 6 12 12M6 18 18 6'],
  link: [
    'm10 13 4-4M8 16l-1 1a4 4 0 0 1-6-6l5-5a4 4 0 0 1 6 0m0 2 1-1a4 4 0 0 1 6 6l-5 5a4 4 0 0 1-6 0',
  ],
  chat: [
    'M21 11a9 9 0 0 1-9 9 10 10 0 0 1-4-.8L3 21l1.8-5A9 9 0 1 1 21 11Z',
    'M8 11h.01M12 11h.01M16 11h.01',
  ],
  users: [
    'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8M22 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8',
  ],
  volume: ['m11 5-6 4H2v6h3l6 4V5ZM15 8a6 6 0 0 1 0 8m3-11a10 10 0 0 1 0 14'],
  'volume-off': ['m11 5-6 4H2v6h3l6 4V5Zm6 4 6 6m-6 0 6-6'],
  sun: [
    'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1 1m12 12 1 1M5 19l1-1M18 6l1-1',
  ],
  moon: ['M21 13a9 9 0 0 1-10-10A9 9 0 1 0 21 13Z'],
  shield: ['m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z', 'm8 12 3 3 5-6'],
  sparkle: ['m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3Z'],
  clock: ['M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18M12 7v5l3 2'],
  hand: [
    'M8 13V5a2 2 0 0 1 4 0v7-9a2 2 0 0 1 4 0v9-6a2 2 0 0 1 4 0v9a7 7 0 0 1-12 5l-5-6a2 2 0 0 1 3-3l2 2Z',
  ],
  mail: ['M3 5h18v14H3V5Zm0 1 9 7 9-7'],
  jira: ['m12 2 10 10-10 10L2 12 10 2Z', 'm12 8 4 4-4 4-4-4 4-4Z'],
  expand: ['M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5'],
  help: ['M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18M9.5 9a2.5 2.5 0 1 1 4 2c-1 .6-1.5 1-1.5 2M12 16h.01'],
  leave: ['M10 17v3H3V4h7v3M8 12h13m-4-4 4 4-4 4'],
  headphones: ['M3 14v-3a9 9 0 0 1 18 0v3M3 12h4v8H3v-8Zm14 0h4v8h-4v-8Z'],
  chart: ['M3 3v18h18', 'm7 15 4-4 3 3 5-6'],
  building: ['M4 21V5a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v16M16 9h2a2 2 0 0 1 2 2v10M2 21h20', 'M8 7h4M8 11h4M8 15h4'],
  database: ['M12 3c4.4 0 8 1.3 8 3s-3.6 3-8 3-8-1.3-8-3 3.6-3 8-3Z', 'M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3'],
  lock: ['M6 11h12a1 1 0 0 1 1 1v8a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1v-8a1 1 0 0 1 1-1Z', 'M8 11V7a4 4 0 0 1 8 0v4'],
  logout: ['M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9'],
  trash: ['M3 6h18M8 6V4h8v2m-9 0 1 14h8l1-14'],
  upload: ['M12 15V3m-5 5 5-5 5 5', 'M4 15v4a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-4'],
  copy: ['M9 9h11v11H9z', 'M5 15H4V4h11v1'],
  refresh: ['M20 11a8 8 0 0 0-14.9-4M4 4v4h4M4 13a8 8 0 0 0 14.9 4M20 20v-4h-4'],
};

@Component({
  selector: 'tn-icon',
  host: { 'aria-hidden': 'true' },
  template: `<svg
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.65"
    stroke-linecap="round"
    stroke-linejoin="round"
  >
    @for (path of paths[name()] ?? paths['sparkle']; track $index) {
      <path [attr.d]="path" />
    }
  </svg>`,
})
export class Icon {
  readonly name = input('sparkle');
  readonly paths = PATHS;
}
