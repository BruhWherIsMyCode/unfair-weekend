# Claude Design export

Design for the family app, from the Claude Design project "Onas Calls Demo v5".

- `onas-calls-demo-v5.html` is the published artifact, saved unchanged. Open it in a browser to see the design. It is the source of truth.
- `unpacked/template.html` is the design markup taken out of that file, so it can be read and searched as text: layout, styles, colours, copy, and the small script that steps through the screens.

The unpacked copy does not run on its own. It needs the Claude Design runtime, which is packed inside the export. `{{ ... }}` marks values the runtime fills in.

Do not edit the unpacked copy by hand. When the design changes, replace the export and extract the markup again.

## Parent call screens

- `client-call-video.html`: the Claude Design "Client Call Video" for the parent (бабушка) app, saved unchanged. Open it in a browser.
- `unpacked/client-video.jsx`: the design source taken out of that file: sizes, colours, copy and motion.
- `client-call-motion.md`: the motion rules for the app, taken from that source.
