// Build the small files used by the default and conference rankings.
const fs = require("fs");
const path = require("path");
const directory = process.argv[2];
const data = JSON.parse(fs.readFileSync(path.join(directory, "citations.json"), "utf8"));
const output = path.join(directory, "ranking");
fs.mkdirSync(output, { recursive: true });
const venues = new Map();
const areas = {};
const papers = data.papers.sort((a, b) =>
  b.citations - a.citations || (b.influential_citations || 0) - (a.influential_citations || 0) ||
  a.title.localeCompare(b.title)
);
for (const paper of papers) {
  for (const venue of new Set(paper.notes.map(note => note.conference))) {
    if (!venues.has(venue)) venues.set(venue, []);
    venues.get(venue).push(paper);
  }
  for (const note of paper.notes) {
    if (!areas[note.conference]) areas[note.conference] = new Set();
    areas[note.conference].add(note.area);
  }
}
function save(name, value) {
  fs.writeFileSync(path.join(output, name + ".json"), JSON.stringify(value));
}
const scopes = {};
for (const [venue, entries] of [["all", papers], ...venues]) {
  const key = venue === "all" ? "all" : "v" + Object.keys(scopes).length;
  scopes[venue] = {key, count: entries.length};
  for (let i = 0; i < entries.length; i += 50) {
    save(key + "-" + (1 + i / 50), entries.slice(i, i + 50));
  }
  if (venue !== "all") save(key, entries);
}
save("index", {matched_works: data.matched_works, generated_at: data.generated_at, scopes,
  areas: Object.fromEntries(Object.entries(areas).map(([v, names]) => [v, [...names]]))});
