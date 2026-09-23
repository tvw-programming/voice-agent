// Vani student API: the simplest Express server with static sample data.
//
//   npm install
//   npm start                      # http://127.0.0.1:3000
//
// Endpoints (all under /api/v1, all need "Authorization: Bearer <STUDENT_API_KEY>"):
//   GET /api/v1/students/search?first_name=&last_name=&class=&division=&roll_number=
//   GET /api/v1/students/:id
// Plus an open health check:
//   GET /health
//
// Search rules:
//   * at least one parameter is required
//   * names match exactly, ignoring case ("priya" = "Priya"); fuzzy and sound-based
//     matching happen in the voice agent, not here
//   * class, division and roll_number are exact matches; all given parameters must match
//
// Responses: { "results": [...], "count": n } for search, { "data": {...} } for one student,
// { "error": "..." } with status 400 / 401 / 404 on errors.

const express = require("express");
const students = require("./students");

const API_KEY = process.env.STUDENT_API_KEY || "dev-key";
const SEARCH_PARAMS = ["first_name", "last_name", "class", "division", "roll_number"];
const MAX_RESULTS = 50;

const app = express();
app.disable("x-powered-by");

app.get("/health", (req, res) => {
  res.json({ status: "ok", students: students.length });
});

// Every /api route needs the bearer token.
app.use("/api", (req, res, next) => {
  if (req.get("authorization") !== `Bearer ${API_KEY}`) {
    return res.status(401).json({ error: "missing or wrong API key" });
  }
  next();
});

const same = (a, b) => String(a).trim().toLowerCase() === String(b).trim().toLowerCase();

app.get("/api/v1/students/search", (req, res) => {
  const q = {};
  for (const name of SEARCH_PARAMS) {
    const value = req.query[name];
    if (typeof value === "string" && value.trim() !== "") q[name] = value.trim();
  }
  if (Object.keys(q).length === 0) {
    return res.status(400).json({ error: `give at least one of: ${SEARCH_PARAMS.join(", ")}` });
  }
  if (q.roll_number && !/^\d+$/.test(q.roll_number)) {
    return res.status(400).json({ error: "roll_number must be digits" });
  }
  const results = students
    .filter((s) => Object.entries(q).every(([field, value]) => same(s[field], value)))
    .slice(0, MAX_RESULTS);
  res.json({ results, count: results.length });
});

app.get("/api/v1/students/:id", (req, res) => {
  const student = students.find((s) => String(s.id) === req.params.id);
  if (!student) return res.status(404).json({ error: "student not found" });
  res.json({ data: student });
});

app.use((req, res) => res.status(404).json({ error: "not found" }));

if (require.main === module) {
  const port = Number(process.env.PORT) || 3000;
  const host = process.env.HOST || "127.0.0.1";
  app.listen(port, host, () => {
    console.log(`Student API on http://${host}:${port}  (${students.length} students, key: ${API_KEY === "dev-key" ? "dev-key (default)" : "from STUDENT_API_KEY"})`);
  });
}

module.exports = app;
