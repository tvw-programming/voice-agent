"""Mock student API for local testing:  python mock_api/server.py  (port 8001)."""
from fastapi import FastAPI, Header, HTTPException, Query
from rapidfuzz import fuzz

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
]


@app.get("/students/search")
def search(first_name: str = Query(""), last_name: str = Query(""), authorization: str | None = Header(None)):
    if authorization != "Bearer mock-key":
        raise HTTPException(401, "bad key")
    def ok(s):
        fn = not first_name or fuzz.ratio(first_name.lower(), s["first_name"].lower()) >= 80
        ln = not last_name or fuzz.ratio(last_name.lower(), s["last_name"].lower()) >= 80
        return fn and ln
    return {"results": [s for s in STUDENTS if ok(s)]}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8001)
