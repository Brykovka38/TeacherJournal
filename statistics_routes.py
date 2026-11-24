from datetime import date
from collections import defaultdict
from decimal import Decimal
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

router = APIRouter()

def project_of(topic):
    # The first dot separates project name from the action.
    if "." not in topic:
        return "Без проекта", topic
    project, action = topic.split(".", 1)
    if not project.strip() or not action.strip():
        return "Без проекта", topic
    return project.strip(), action.strip()

@router.get("/statistics", response_class=HTMLResponse)
def statistics(request: Request, student_id: str = "", subject_id: str = "",
               project: str = "", topic_id: str = "",
               date_from: str = "", date_to: str = ""):
    from main import engine, templates
    try:
        sid = int(student_id) if student_id else None
        subid = int(subject_id) if subject_id else None
        tid = int(topic_id) if topic_id else None
        start = date.fromisoformat(date_from) if date_from else None
        end = date.fromisoformat(date_to) if date_to else None
    except ValueError:
        raise HTTPException(400, "Некорректное значение фильтра")
    if start and end and start > end:
        raise HTTPException(400, "Начальная дата позже конечной")
    with engine.connect() as conn:
        students = conn.execute(text("SELECT id,last_name,first_name FROM students ORDER BY last_name,first_name")).mappings().all()
        subjects = conn.execute(text("SELECT id,name FROM subjects ORDER BY name")).mappings().all()
        topics = conn.execute(text("SELECT id,subject_id,name FROM topics ORDER BY name")).mappings().all()
        if sid is not None and not any(s["id"] == sid for s in students):
            raise HTTPException(404, "Ученик не найден")
        if subid is not None and not any(s["id"] == subid for s in subjects):
            raise HTTPException(404, "Предмет не найден")
        if tid is not None and not any(t["id"] == tid for t in topics):
            raise HTTPException(404, "Тема не найдена")
        data = conn.execute(text("""
            SELECT e.student_id, s.last_name, s.first_name,
                   e.subject_id, sub.name AS subject_name,
                   e.topic_id, t.name AS topic_name,
                   SUM(e.planned_academic_hours) AS planned,
                   SUM(CASE WHEN e.attendance='present' THEN e.planned_academic_hours ELSE 0 END) AS attended,
                   SUM(CASE WHEN e.attendance='absent' THEN COALESCE(m.worked,0) ELSE 0 END) AS made_up,
                   SUM(CASE WHEN e.attendance='absent' THEN
                         GREATEST(e.planned_academic_hours-COALESCE(m.worked,0),0)
                         ELSE 0 END) AS remaining
            FROM lesson_entries e
            JOIN lessons l ON l.id=e.lesson_id
            JOIN students s ON s.id=e.student_id
            JOIN subjects sub ON sub.id=e.subject_id
            JOIN topics t ON t.id=e.topic_id
            LEFT JOIN LATERAL (
                SELECT SUM(academic_hours) AS worked
                FROM makeup_sessions WHERE missed_entry_id=e.id
            ) m ON TRUE
            WHERE (:sid IS NULL OR e.student_id=:sid)
              AND (:subid IS NULL OR e.subject_id=:subid)
              AND (:tid IS NULL OR e.topic_id=:tid)
              AND (CAST(:start AS date) IS NULL OR l.lesson_date>=CAST(:start AS date))
              AND (CAST(:end AS date) IS NULL OR l.lesson_date<=CAST(:end AS date))
            GROUP BY e.student_id,s.last_name,s.first_name,e.subject_id,sub.name,e.topic_id,t.name
            ORDER BY s.last_name,s.first_name,sub.name,t.name
        """), {"sid":sid,"subid":subid,"tid":tid,"start":start,"end":end}).mappings().all()

    # Filter projects after splitting the topic name; retain the full subject catalogue.
    project_options = sorted({project_of(t["name"])[0] for t in topics
                              if subid is None or t["subject_id"] == subid}, key=str.casefold)
    rows = []
    for record in data:
        p, action = project_of(record["topic_name"])
        if project and p != project:
            continue
        item = dict(record)
        item["project"] = p
        item["action"] = action
        item["completed"] = item["attended"] + item["made_up"]
        rows.append(item)

    # Hierarchy: student -> subject -> project -> topic.
    groups = {}
    for r in rows:
        key = (r["student_id"], r["subject_id"])
        if key not in groups:
            groups[key] = {"student_name":f'{r["last_name"]} {r["first_name"]}',
                           "subject_name":r["subject_name"], "projects":{},
                           "completed":Decimal(0),"planned":Decimal(0),"remaining":Decimal(0)}
        g = groups[key]
        if r["project"] not in g["projects"]:
            g["projects"][r["project"]] = {"name":r["project"],"topics":[],
                                           "completed":Decimal(0),"planned":Decimal(0),"remaining":Decimal(0)}
        p = g["projects"][r["project"]]
        p["topics"].append(r)
        for target in (g,p):
            for k in ("completed","planned","remaining"):
                target[k] += r[k]
    group_list = list(groups.values())
    for g in group_list:
        g["projects"] = sorted(g["projects"].values(),key=lambda x:x["name"].casefold())
    totals = {k:sum((r[k] for r in rows),Decimal(0)) for k in ("completed","planned","remaining","attended","made_up")}
    topic_options = [dict(t) for t in topics if subid is None or t["subject_id"]==subid]
    return templates.TemplateResponse(request,"statistics.html",{
        "students":students,"subjects":subjects,"topics":topic_options,
        "student_id":sid,"subject_id":subid,"topic_id":tid,"project":project,
        "project_options":project_options,"date_from":date_from,"date_to":date_to,
        "groups":group_list,"totals":totals
    })
