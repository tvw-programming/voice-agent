"""Mock student API for local testing:  python mock_api/server.py  (port 8001).

GET /students/search?first_name=&last_name=&class=&division=&roll_number=
Header: Authorization: Bearer mock-key

Names match exactly (case-insensitive), like most real school APIs, so the
agent's sound-based surname variants (Desmukh -> Deshmukh) get exercised.
"""
import random

from fastapi import FastAPI, Header, HTTPException, Query

app = FastAPI(title="Mock Student API")

STUDENTS = [
    {"id": 1, "first_name": "Aarav", "last_name": "Deshmukh", "class": "8", "division": "A", "roll_number": 12,
     "attendance_percent": 94.5, "last_exam_result": "First class, 86 percent",
     "phone": "9800000001", "address": "Gangapur Road, Nashik", "date_of_birth": "2012-04-02"},
    {"id": 2, "first_name": "Aarav", "last_name": "Deshmukh", "class": "6", "division": "C", "roll_number": 4,
     "attendance_percent": 88.0, "last_exam_result": "Distinction, 91 percent",
     "phone": "9800000002", "address": "College Road, Nashik", "date_of_birth": "2014-09-17"},
    {"id": 3, "first_name": "Priya", "last_name": "Kulkarni", "class": "10", "division": "B", "roll_number": 27,
     "attendance_percent": 97.2, "last_exam_result": "Distinction, 93 percent",
     "phone": "9800000003", "address": "Panchavati, Nashik", "date_of_birth": "2010-01-25"},
    {"id": 4, "first_name": "Rohan", "last_name": "Patil", "class": "9", "division": "A", "roll_number": 31,
     "attendance_percent": 79.4, "last_exam_result": "Second class, 58 percent",
     "phone": "9800000004", "address": "Cidco, Nashik", "date_of_birth": "2011-07-08"},
    {"id": 5, "first_name": "Sneha", "last_name": "Joshi", "class": "7", "division": "D", "roll_number": 9,
     "attendance_percent": 91.0, "last_exam_result": "First class, 74 percent",
     "phone": "9800000005", "address": "Satpur, Nashik", "date_of_birth": "2013-11-30"},
    # Tricky cases for Features 2 and 4
    {"id": 6, "first_name": "Rohan", "last_name": "Patel", "class": "9", "division": "C", "roll_number": 18,
     "attendance_percent": 85.0, "last_exam_result": "First class, 68 percent", "phone": "9800000006"},
    {"id": 7, "first_name": "Aarav", "last_name": "Joshi", "class": "7", "division": "B", "roll_number": 2,
     "attendance_percent": 90.1, "last_exam_result": "First class, 71 percent", "phone": "9800000007"},
    {"id": 8, "first_name": "Ishaan", "last_name": "Bhatt", "class": "8", "division": "A", "roll_number": 5,
     "attendance_percent": 96.3, "last_exam_result": "Distinction, 88 percent", "phone": "9800000008"},
    {"id": 9, "first_name": "Ananya", "last_name": "Chaudhary", "class": "8", "division": "B", "roll_number": 3,
     "attendance_percent": 93.0, "last_exam_result": "Distinction, 90 percent", "phone": "9800000009"},
    {"id": 10, "first_name": "Kavya", "last_name": "Shinde", "class": "UKG", "division": "A", "roll_number": 7,
     "attendance_percent": 89.0, "last_exam_result": "Very good", "phone": "9800000010"},
]

# Filler students so class/roll searches look realistic (deterministic).
_rng = random.Random(7)
_FIRST = ["Aditya", "Diya", "Vihaan", "Saanvi", "Arjun", "Myra", "Reyansh", "Anika", "Kabir", "Tanvi",
          "Om", "Pari", "Yash", "Riya", "Atharva", "Gauri", "Soham", "Mitali", "Parth", "Shruti"]
_LAST = ["Pawar", "Jadhav", "Gaikwad", "More", "Kale", "Sawant", "Wagh", "Salunkhe", "Thakur", "Nair",
         "Iyer", "Reddy", "Kamble", "Deshpande", "Gokhale"]
for i in range(11, 41):
    cls = str(_rng.randint(5, 10))
    div = _rng.choice("ABCD")
    STUDENTS.append({"id": i, "first_name": _rng.choice(_FIRST), "last_name": _rng.choice(_LAST),
                     "class": cls, "division": div, "roll_number": 40 + i,
                     "attendance_percent": round(_rng.uniform(70, 99), 1),
                     "last_exam_result": _rng.choice(["Distinction", "First class", "Second class"]),
                     "phone": f"98{i:08d}"})


@app.get("/students/search")
def search(first_name: str = Query(""), last_name: str = Query(""),
           class_: str = Query("", alias="class"), division: str = Query(""), roll_number: str = Query(""),
           authorization: str | None = Header(None)):
    if authorization != "Bearer mock-key":
        raise HTTPException(401, "bad key")
    if not any([first_name, last_name, class_, division, roll_number]):
        raise HTTPException(400, "give at least one search parameter")

    def ok(s):
        return ((not first_name or s["first_name"].lower() == first_name.lower())
                and (not last_name or s["last_name"].lower() == last_name.lower())
                and (not class_ or str(s["class"]).lower() == class_.lower())
                and (not division or s["division"].lower() == division.lower())
                and (not roll_number or str(s["roll_number"]) == roll_number))
    return {"results": [s for s in STUDENTS if ok(s)]}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8001)
