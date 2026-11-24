import os
from datetime import date
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi import Request
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.engine import URL
from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / '.env')
url = URL.create('postgresql+psycopg2', username=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'), host=os.getenv('DB_HOST','localhost'), port=int(os.getenv('DB_PORT','5432')), database=os.getenv('DB_NAME','teacher_journal'))
# Render supplies a PostgreSQL connection URL through the environment.
cloud_url = os.getenv('DATABASE_URL')
if cloud_url:
    from sqlalchemy.engine import make_url
    url = make_url(cloud_url).set(drivername='postgresql+psycopg2')
engine = create_engine(url, pool_pre_ping=True)
app = FastAPI(title='Журнал преподавателя')
app.mount('/static', StaticFiles(directory=BASE/'static'), name='static')
templates = Jinja2Templates(directory=BASE/'templates')

def rows(sql, params=None):
    with engine.connect() as conn:
        return conn.execute(text(sql),params or {}).mappings().all()

def redirect():
    return RedirectResponse('/', status_code=303)

@app.get('/', response_class=HTMLResponse)
def home(request: Request):
    groups = rows('SELECT id, name, active FROM study_groups ORDER BY active DESC, name')
    students = rows('SELECT id, last_name, first_name, current_course, active, EXISTS (SELECT 1 FROM lesson_entries e WHERE e.student_id=students.id) AS has_history FROM students ORDER BY active DESC, last_name, first_name')
    memberships = rows('''SELECT gm.group_id, gm.student_id, gm.joined_on, gm.left_on, s.last_name, s.first_name FROM group_memberships gm JOIN students s ON s.id=gm.student_id WHERE gm.left_on IS NULL AND s.active ORDER BY s.last_name,s.first_name''')
    return templates.TemplateResponse(request, 'index.html', {'groups':groups,'students':students,'memberships':memberships})

@app.get('/check-db')
def check_db():
    with engine.connect() as conn:
        db=conn.execute(text('SELECT current_database()')).scalar()
        n=conn.execute(text("SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'")).scalar()
    return {'status':'connected','database':db,'tables':n}

@app.post('/groups')
def add_group(name: str=Form(...)):
    name=name.strip()
    if not name: raise HTTPException(400,'Название группы обязательно')
    with engine.begin() as conn:
        conn.execute(text('INSERT INTO study_groups (name,active) VALUES (:name,TRUE)'),{'name':name})
    return redirect()

@app.post('/groups/{group_id}/rename')
def rename_group(group_id:int,name:str=Form(...)):
    name=name.strip()
    if not name: raise HTTPException(400,'Название группы обязательно')
    with engine.begin() as conn:
        conn.execute(text('UPDATE study_groups SET name=:name WHERE id=:id'),{'name':name,'id':group_id})
    return redirect()

@app.post('/students')
def add_student(last_name:str=Form(...),first_name:str=Form(...),course:int=Form(...),group_id:int=Form(...)):
    if not (1<=course<=5): raise HTTPException(400,'Курс должен быть от 1 до 5')
    if not last_name.strip() or not first_name.strip(): raise HTTPException(400,'Имя и фамилия обязательны')
    with engine.begin() as conn:
        exists=conn.execute(text('SELECT id FROM study_groups WHERE id=:id AND active=TRUE'),{'id':group_id}).scalar()
        if exists is None: raise HTTPException(400,'Группа не найдена')
        student_id=conn.execute(text('INSERT INTO students(last_name,first_name,current_course,active) VALUES (:last,:first,:course,TRUE) RETURNING id'),{'last':last_name.strip(),'first':first_name.strip(),'course':course}).scalar_one()
        conn.execute(text('INSERT INTO group_memberships(group_id,student_id,joined_on) VALUES (:gid,:sid,:today)'),{'gid':group_id,'sid':student_id,'today':date.today()})
    return redirect()

@app.post('/students/{student_id}/course')
def change_course(student_id:int,course:int=Form(...)):
    if not (1<=course<=5): raise HTTPException(400,'Курс должен быть от 1 до 5')
    with engine.begin() as conn:
        conn.execute(text('UPDATE students SET current_course=:course WHERE id=:id'),{'course':course,'id':student_id})
    return redirect()

@app.post('/students/{student_id}/edit')
def edit_student(student_id:int,last_name:str=Form(...),first_name:str=Form(...),course:int=Form(...)):
    if not last_name.strip() or not first_name.strip() or not 1<=course<=5:
        raise HTTPException(400,'Укажите имя, фамилию и курс от 1 до 5')
    with engine.begin() as conn:
        result=conn.execute(text('UPDATE students SET last_name=:last, first_name=:first, current_course=:course WHERE id=:id'),{'last':last_name.strip(),'first':first_name.strip(),'course':course,'id':student_id})
        if not result.rowcount: raise HTTPException(404,'Ученик не найден')
    return redirect()

@app.post('/students/{student_id}/active')
def set_student_active(student_id:int,active:bool=Form(...)):
    with engine.begin() as conn:
        result=conn.execute(text('UPDATE students SET active=:active WHERE id=:id'),{'active':active,'id':student_id})
        if not result.rowcount: raise HTTPException(404,'Ученик не найден')
    return redirect()

@app.post('/students/{student_id}/delete')
def delete_student(student_id:int):
    try:
        with engine.begin() as conn:
            if conn.execute(text('SELECT id FROM students WHERE id=:id FOR UPDATE'),{'id':student_id}).scalar() is None:
                raise HTTPException(404,'Ученик не найден')
            if conn.execute(text('SELECT 1 FROM lesson_entries WHERE student_id=:id LIMIT 1'),{'id':student_id}).scalar():
                raise HTTPException(409,'У ученика есть история занятий. Используйте архивирование.')
            conn.execute(text('DELETE FROM group_memberships WHERE student_id=:id'),{'id':student_id})
            conn.execute(text('DELETE FROM students WHERE id=:id'),{'id':student_id})
    except IntegrityError:
        raise HTTPException(409,'Удаление невозможно: существуют связанные записи. Используйте архивирование.')
    return redirect()

@app.post('/memberships')
def add_membership(student_id:int=Form(...),group_id:int=Form(...)):
    with engine.begin() as conn:
        if not conn.execute(text('SELECT id FROM students WHERE id=:id AND active'),{'id':student_id}).scalar():
            raise HTTPException(400,'Активный ученик не найден')
        exists=conn.execute(text('SELECT 1 FROM group_memberships WHERE student_id=:sid AND group_id=:gid AND left_on IS NULL'),{'sid':student_id,'gid':group_id}).scalar()
        if not exists:
            conn.execute(text('INSERT INTO group_memberships(student_id,group_id,joined_on) VALUES (:sid,:gid,:today)'),{'sid':student_id,'gid':group_id,'today':date.today()})
    return redirect()

@app.post('/memberships/{student_id}/{group_id}/leave')
def leave_group(student_id:int,group_id:int):
    with engine.begin() as conn:
        conn.execute(text('UPDATE group_memberships SET left_on=:today WHERE student_id=:sid AND group_id=:gid AND left_on IS NULL'),{'today':date.today(),'sid':student_id,'gid':group_id})
    return redirect()


# Каталог предметов и тем. Все темы доступны на любом курсе.
@app.get('/catalog', response_class=HTMLResponse)
def catalog(request: Request):
    subjects = rows('SELECT id, name, archived FROM subjects ORDER BY archived, name')
    topics = rows('SELECT id, subject_id, name, archived FROM topics ORDER BY archived, name')
    return templates.TemplateResponse(request, 'catalog.html', {'subjects':subjects, 'topics':topics})

def catalog_redirect(subject_id=None):
    return RedirectResponse('/catalog' + (f'?subject_id={subject_id}' if subject_id else ''), status_code=303)

@app.post('/catalog/subjects')
def create_subject(name: str=Form(...)):
    name=name.strip()
    if not name: raise HTTPException(400,'Введите название предмета')
    with engine.begin() as conn:
        existing=conn.execute(text('SELECT id FROM subjects WHERE lower(name)=lower(:name)'),{'name':name}).scalar()
        if existing: raise HTTPException(409,'Такой предмет уже существует')
        conn.execute(text('INSERT INTO subjects(name) VALUES (:name)'),{'name':name})
    return catalog_redirect()

@app.post('/catalog/subjects/{subject_id}/rename')
def rename_subject(subject_id:int,name:str=Form(...)):
    name=name.strip()
    if not name: raise HTTPException(400,'Введите название предмета')
    with engine.begin() as conn:
        existing=conn.execute(text('SELECT id FROM subjects WHERE lower(name)=lower(:name) AND id<>:id'),{'name':name,'id':subject_id}).scalar()
        if existing: raise HTTPException(409,'Такой предмет уже существует')
        result=conn.execute(text('UPDATE subjects SET name=:name WHERE id=:id'),{'name':name,'id':subject_id})
        if not result.rowcount: raise HTTPException(404,'Предмет не найден')
    return catalog_redirect(subject_id)

@app.post('/catalog/subjects/{subject_id}/archive')
def archive_subject(subject_id:int,archived:bool=Form(...)):
    with engine.begin() as conn:
        conn.execute(text('UPDATE subjects SET archived=:archived WHERE id=:id'),{'archived':archived,'id':subject_id})
    return catalog_redirect(subject_id)

@app.post('/catalog/topics')
def create_topic(subject_id:int=Form(...),name:str=Form(...)):
    name=name.strip()
    if not name: raise HTTPException(400,'Введите название темы')
    with engine.begin() as conn:
        subject=conn.execute(text('SELECT id FROM subjects WHERE id=:id AND archived=FALSE'),{'id':subject_id}).scalar()
        if subject is None: raise HTTPException(400,'Предмет не найден или архивирован')
        existing=conn.execute(text('SELECT id FROM topics WHERE subject_id=:sid AND lower(name)=lower(:name)'),{'sid':subject_id,'name':name}).scalar()
        if existing: raise HTTPException(409,'Такая тема уже существует в этом предмете')
        conn.execute(text('INSERT INTO topics(subject_id,name) VALUES (:sid,:name)'),{'sid':subject_id,'name':name})
    return catalog_redirect(subject_id)

@app.post('/catalog/topics/{topic_id}/rename')
def rename_topic(topic_id:int,name:str=Form(...)):
    name=name.strip()
    if not name: raise HTTPException(400,'Введите название темы')
    with engine.begin() as conn:
        topic=conn.execute(text('SELECT subject_id FROM topics WHERE id=:id'),{'id':topic_id}).scalar()
        if topic is None: raise HTTPException(404,'Тема не найдена')
        duplicate=conn.execute(text('SELECT id FROM topics WHERE subject_id=:sid AND lower(name)=lower(:name) AND id<>:id'),{'sid':topic,'name':name,'id':topic_id}).scalar()
        if duplicate: raise HTTPException(409,'Такая тема уже существует в этом предмете')
        conn.execute(text('UPDATE topics SET name=:name WHERE id=:id'),{'name':name,'id':topic_id})
    return catalog_redirect(topic)

@app.post('/catalog/topics/{topic_id}/archive')
def archive_topic(topic_id:int,archived:bool=Form(...)):
    with engine.begin() as conn:
        topic=conn.execute(text('UPDATE topics SET archived=:archived WHERE id=:id RETURNING subject_id'),{'archived':archived,'id':topic_id}).scalar()
    return catalog_redirect(topic)

# Журнал занятий. Каждая запись ученика хранится отдельно.
from decimal import Decimal
from datetime import datetime
from fastapi.responses import JSONResponse

@app.get('/journal', response_class=HTMLResponse)
def journal(request: Request, group_id: int | None = None, lesson_date: date | None = None):
    day = lesson_date or date.today()
    groups = rows('SELECT id,name FROM study_groups WHERE active=TRUE ORDER BY name')
    if group_id is None and groups:
        group_id = groups[0]['id']
    if group_id is not None and not any(g['id']==group_id for g in groups):
        raise HTTPException(404, 'Группа не найдена')
    members = rows('''SELECT DISTINCT ON (s.id) s.id, s.first_name, s.last_name,
          COALESCE(saved.course_at_lesson, s.current_course) AS current_course
      FROM students s
      LEFT JOIN LATERAL (
          SELECT e.course_at_lesson FROM lesson_entries e
          JOIN lessons l ON l.id=e.lesson_id
          WHERE e.student_id=s.id AND l.group_id=:gid AND l.lesson_date=:day
          ORDER BY e.id DESC LIMIT 1
      ) saved ON TRUE
      WHERE (s.active AND EXISTS (
          SELECT 1 FROM group_memberships gm
          WHERE gm.student_id=s.id AND gm.group_id=:gid AND gm.left_on IS NULL
      )) OR saved.course_at_lesson IS NOT NULL
      ORDER BY s.id''', {'gid':group_id,'day':day}) if group_id else []
    subjects = rows('SELECT id,name FROM subjects WHERE archived=FALSE ORDER BY name')
    topics = rows('''SELECT t.id,t.subject_id,t.name FROM topics t JOIN subjects s ON s.id=t.subject_id
      WHERE t.archived=FALSE AND s.archived=FALSE ORDER BY t.name''')
    lesson = rows('SELECT id,notes FROM lessons WHERE group_id=:gid AND lesson_date=:day ORDER BY id DESC LIMIT 1', {'gid':group_id,'day':day}) if group_id else []
    lesson_id = lesson[0]['id'] if lesson else None
    entries = rows('SELECT id,student_id,course_at_lesson,subject_id,topic_id,planned_academic_hours,attendance,comment FROM lesson_entries WHERE lesson_id=:lid',{'lid':lesson_id}) if lesson_id else []
    plan = rows('SELECT subject_id,topic_id,planned_academic_hours FROM first_year_plans WHERE lesson_id=:lid',{'lid':lesson_id}) if lesson_id else []
    previous = rows('''SELECT DISTINCT ON (e.student_id) e.student_id,e.subject_id,e.topic_id,e.planned_academic_hours
      FROM lesson_entries e JOIN lessons l ON l.id=e.lesson_id
      WHERE e.student_id=ANY(:ids) AND l.lesson_date<:day AND e.attendance='present'
      ORDER BY e.student_id,l.lesson_date DESC,e.id DESC''', {'ids':[m['id'] for m in members], 'day':day}) if members else []
    balances = rows('''SELECT b.lesson_entry_id,b.student_id,b.missed_date,b.required_hours,b.worked_hours,b.remaining_hours,b.makeup_status,
      s.first_name,s.last_name,t.name AS topic_name
      FROM missed_hours_balance b JOIN students s ON s.id=b.student_id
      JOIN lesson_entries e ON e.id=b.lesson_entry_id JOIN topics t ON t.id=e.topic_id
      WHERE b.remaining_hours>0 ORDER BY b.missed_date,s.last_name''')
    return templates.TemplateResponse(request,'journal.html',{
      'groups':groups,'group_id':group_id,'day':day.isoformat(),'members':members,
      'subjects':subjects,'topics':topics,'lesson_id':lesson_id,'entries':entries,
      'plan':plan[0] if plan else None,'previous':previous,'balances':balances,
      'lesson_notes':lesson[0]['notes'] if lesson else '',
      'journal_json': __import__('json').dumps({'members':[dict(x) for x in members], 'subjects':[dict(x) for x in subjects], 'topics':[dict(x) for x in topics], 'entries':[dict(x) for x in entries], 'previous':[dict(x) for x in previous], 'plan':dict(plan[0]) if plan else None}, default=str).replace('<','\\u003c')})

@app.post('/journal/save')
def save_journal(group_id:int=Form(...),lesson_date:date=Form(...),payload:str=Form(...)):
    import json
    try:
        data=json.loads(payload)
    except ValueError:
        raise HTTPException(400,'Неверный формат данных')
    if not isinstance(data,dict) or not isinstance(data.get('entries'),list):
        raise HTTPException(400,'Нет записей занятия')
    if not data['entries']:
        raise HTTPException(400,'Нужно указать хотя бы одного ученика')
    shared=data.get('shared')
    with engine.begin() as conn:
        group=conn.execute(text('SELECT id FROM study_groups WHERE id=:gid'),{'gid':group_id}).scalar()
        if not group: raise HTTPException(404,'Группа не найдена')
        # Prevent concurrent duplicate saves for one group/date.
        conn.execute(text('SELECT pg_advisory_xact_lock(:gid, :daynum)'),{'gid':group_id,'daynum':(lesson_date-date(2000,1,1)).days})
        lesson_id=conn.execute(text('SELECT id FROM lessons WHERE group_id=:gid AND lesson_date=:day ORDER BY id DESC LIMIT 1'),{'gid':group_id,'day':lesson_date}).scalar()
        if lesson_id is None:
            lesson_id=conn.execute(text('INSERT INTO lessons(group_id,lesson_date) VALUES (:gid,:day) RETURNING id'),{'gid':group_id,'day':lesson_date}).scalar_one()
        if shared:
            shared_subject=int(shared['subject_id']); shared_topic=int(shared['topic_id']); shared_hours=Decimal(str(shared['hours']))
            if shared_hours<=0 or shared_hours>24: raise HTTPException(400,'Некорректные часы общей темы')
            valid=conn.execute(text('''SELECT 1 FROM topics t JOIN subjects s ON s.id=t.subject_id WHERE t.id=:tid AND t.subject_id=:sid AND NOT t.archived AND NOT s.archived'''),{'tid':shared_topic,'sid':shared_subject}).scalar()
            if not valid: raise HTTPException(400,'Общая тема не найдена')
            conn.execute(text('''INSERT INTO first_year_plans(lesson_id,subject_id,topic_id,planned_academic_hours)
              VALUES (:lid,:sid,:tid,:hrs) ON CONFLICT(lesson_id) DO UPDATE SET subject_id=EXCLUDED.subject_id,topic_id=EXCLUDED.topic_id,planned_academic_hours=EXCLUDED.planned_academic_hours'''),{'lid':lesson_id,'sid':shared_subject,'tid':shared_topic,'hrs':shared_hours})
        else:
            conn.execute(text('DELETE FROM first_year_plans WHERE lesson_id=:lid'),{'lid':lesson_id})
        seen=set()
        for item in data['entries']:
            sid=int(item['student_id'])
            if sid in seen: raise HTTPException(400,'Ученик указан дважды')
            seen.add(sid)
            member=conn.execute(text('''SELECT s.current_course FROM students s
              WHERE s.id=:sid AND (
                (s.active AND EXISTS (SELECT 1 FROM group_memberships gm
                        WHERE gm.student_id=s.id AND gm.group_id=:gid AND gm.left_on IS NULL))
                OR EXISTS (SELECT 1 FROM lesson_entries e JOIN lessons l ON l.id=e.lesson_id
                           WHERE e.student_id=s.id AND l.group_id=:gid AND l.lesson_date=:day)
              )'''),{'sid':sid,'gid':group_id,'day':lesson_date}).scalar()
            if member is None: raise HTTPException(400,f'Ученик {sid} не относится к выбранной группе')
            old=conn.execute(text('SELECT id,course_at_lesson FROM lesson_entries WHERE lesson_id=:lid AND student_id=:sid FOR UPDATE'),{'lid':lesson_id,'sid':sid}).mappings().first()
            course=old['course_at_lesson'] if old else member
            if course==1:
                if not shared: raise HTTPException(400,'Для первокурсников нужна общая тема')
                subject_id,topic_id,hrs=shared_subject,shared_topic,shared_hours
            else:
                subject_id=int(item['subject_id']);topic_id=int(item['topic_id']);hrs=Decimal(str(item['hours']))
            if hrs<=0 or hrs>24: raise HTTPException(400,'Часы должны быть больше 0 и не больше 24')
            valid=conn.execute(text('SELECT 1 FROM topics WHERE id=:tid AND subject_id=:sid'),{'tid':topic_id,'sid':subject_id}).scalar()
            if not valid: raise HTTPException(400,'Тема не относится к предмету')
            attendance=item.get('attendance')
            if attendance not in ('present','absent'): raise HTTPException(400,'Неверный статус посещения')
            comment=str(item.get('comment','')).strip()[:2000]
            if old:
                makeup=conn.execute(text('SELECT COALESCE(SUM(academic_hours),0) FROM makeup_sessions WHERE missed_entry_id=:eid'),{'eid':old['id']}).scalar()
                if makeup and (attendance!='absent' or hrs<makeup):
                    raise HTTPException(400,'У записи есть отработки. Нельзя изменить статус или уменьшить часы ниже отработанных')
                conn.execute(text('''UPDATE lesson_entries SET subject_id=:subject,topic_id=:topic,planned_academic_hours=:hrs,attendance=:attendance,comment=:comment
                  WHERE id=:id'''),{'subject':subject_id,'topic':topic_id,'hrs':hrs,'attendance':attendance,'comment':comment,'id':old['id']})
            else:
                conn.execute(text('''INSERT INTO lesson_entries(lesson_id,student_id,course_at_lesson,subject_id,topic_id,planned_academic_hours,attendance,comment)
                  VALUES (:lid,:sid,:course,:subject,:topic,:hrs,:attendance,:comment)'''),{'lid':lesson_id,'sid':sid,'course':course,'subject':subject_id,'topic':topic_id,'hrs':hrs,'attendance':attendance,'comment':comment})
        # Do not silently remove existing records when an editor unchecks a student.
    return RedirectResponse(f'/journal?group_id={group_id}&lesson_date={lesson_date}&saved=1',status_code=303)

@app.post('/journal/topics')
def journal_add_topic(subject_id:int=Form(...),name:str=Form(...)):
    name=name.strip()
    if not name: raise HTTPException(400,'Введите название темы')
    with engine.begin() as conn:
        subject=conn.execute(text('SELECT id FROM subjects WHERE id=:sid AND archived=FALSE'),{'sid':subject_id}).scalar()
        if not subject: raise HTTPException(400,'Предмет недоступен')
        existing=conn.execute(text('SELECT id FROM topics WHERE subject_id=:sid AND lower(name)=lower(:name)'),{'sid':subject_id,'name':name}).scalar()
        if existing:
            topic_id=existing
        else:
            topic_id=conn.execute(text('INSERT INTO topics(subject_id,name) VALUES (:sid,:name) RETURNING id'),{'sid':subject_id,'name':name}).scalar_one()
    return {'id':topic_id,'subject_id':subject_id,'name':name}

@app.post('/journal/makeup')
def add_makeup(missed_entry_id:int=Form(...),actual_date:date=Form(...),academic_hours:Decimal=Form(...),comment:str=Form('')):
    if academic_hours<=0 or academic_hours>24: raise HTTPException(400,'Некорректные часы')
    with engine.begin() as conn:
        entry=conn.execute(text('''SELECT e.id,e.attendance,e.planned_academic_hours,l.lesson_date FROM lesson_entries e
          JOIN lessons l ON l.id=e.lesson_id WHERE e.id=:id FOR UPDATE OF e'''),{'id':missed_entry_id}).mappings().first()
        if not entry or entry['attendance']!='absent': raise HTTPException(400,'Это не пропущенное занятие')
        if actual_date<entry['lesson_date']: raise HTTPException(400,'Дата отработки раньше пропуска')
        worked=conn.execute(text('SELECT COALESCE(SUM(academic_hours),0) FROM makeup_sessions WHERE missed_entry_id=:id'),{'id':missed_entry_id}).scalar()
        if worked+academic_hours>entry['planned_academic_hours']:
            raise HTTPException(400,f'Можно отработать не больше {entry["planned_academic_hours"]-worked} ак. ч.')
        conn.execute(text('''INSERT INTO makeup_sessions(missed_entry_id,actual_date,academic_hours,comment)
          VALUES (:id,:day,:hours,:comment)'''),{'id':missed_entry_id,'day':actual_date,'hours':academic_hours,'comment':comment.strip()[:2000]})
    return RedirectResponse('/journal#makeups',status_code=303)

@app.get('/journal/makeups',response_class=HTMLResponse)
def makeups(request:Request):
    items=rows('''SELECT b.lesson_entry_id,b.student_id,b.missed_date,b.required_hours,b.worked_hours,b.remaining_hours,b.makeup_status,
      s.first_name,s.last_name,t.name AS topic_name FROM missed_hours_balance b
      JOIN students s ON s.id=b.student_id JOIN lesson_entries e ON e.id=b.lesson_entry_id
      JOIN topics t ON t.id=e.topic_id ORDER BY b.remaining_hours DESC,b.missed_date DESC''')
    sessions=rows('''SELECT m.id,m.missed_entry_id,m.actual_date,m.academic_hours,m.comment
      FROM makeup_sessions m ORDER BY m.actual_date DESC,m.id DESC''')
    return templates.TemplateResponse(request,'makeups.html',{'items':items,'sessions':sessions,'today':date.today().isoformat()})

@app.post('/journal/makeups/{session_id}/delete')
def delete_makeup(session_id:int):
    with engine.begin() as conn:
        conn.execute(text('DELETE FROM makeup_sessions WHERE id=:id'),{'id':session_id})
    return RedirectResponse('/journal/makeups',status_code=303)


# История занятий: просмотр, фильтрация, исправление даты и удаление ошибок.
@app.get('/history', response_class=HTMLResponse)
def history(request: Request, group_id: str | None = None, student_id: str | None = None,
            date_from: str | None = None, date_to: str | None = None):
    # Пустые поля фильтра означают отсутствие ограничения.
    try:
        group_id = int(group_id) if group_id else None
        student_id = int(student_id) if student_id else None
        date_from = date.fromisoformat(date_from) if date_from else None
        date_to = date.fromisoformat(date_to) if date_to else None
    except (ValueError, TypeError):
        raise HTTPException(400, 'Некорректное значение фильтра')
    if date_from and date_to and date_from > date_to:
        raise HTTPException(400, 'Начальная дата позже конечной')
    groups = rows('SELECT id,name FROM study_groups ORDER BY name')
    students = rows('SELECT id,last_name,first_name FROM students ORDER BY last_name,first_name')
    where = []
    params = {}
    if group_id is not None:
        where.append('l.group_id=:gid'); params['gid']=group_id
    if student_id is not None:
        where.append('e.student_id=:sid'); params['sid']=student_id
    if date_from:
        where.append('l.lesson_date>=:from_date'); params['from_date']=date_from
    if date_to:
        where.append('l.lesson_date<=:to_date'); params['to_date']=date_to
    clause = ' WHERE ' + ' AND '.join(where) if where else ''
    items=rows('''SELECT e.id AS entry_id, l.id AS lesson_id, l.lesson_date, l.group_id,
       g.name AS group_name, e.student_id, s.last_name,s.first_name,
       e.course_at_lesson,sub.name AS subject_name,t.name AS topic_name,
       e.planned_academic_hours,e.attendance,e.comment,
       COALESCE((SELECT SUM(m.academic_hours) FROM makeup_sessions m WHERE m.missed_entry_id=e.id),0) AS worked_hours
       FROM lesson_entries e JOIN lessons l ON l.id=e.lesson_id
       JOIN study_groups g ON g.id=l.group_id JOIN students s ON s.id=e.student_id
       JOIN subjects sub ON sub.id=e.subject_id JOIN topics t ON t.id=e.topic_id
       '''+clause+''' ORDER BY l.lesson_date DESC,g.name,s.last_name,s.first_name,e.id DESC LIMIT 500''',params)
    return templates.TemplateResponse(request,'history.html',{
      'items':items,'groups':groups,'students':students,'group_id':group_id,
      'student_id':student_id,'date_from':date_from,'date_to':date_to,
      'notice':request.query_params.get('notice'),
    })

@app.post('/history/lessons/{lesson_id}/date')
def history_change_date(lesson_id:int, new_date:date=Form(...)):
    with engine.begin() as conn:
        lesson=conn.execute(text('SELECT id,group_id,lesson_date FROM lessons WHERE id=:id FOR UPDATE'),{'id':lesson_id}).mappings().first()
        if lesson is None: raise HTTPException(404,'Занятие не найдено')
        if new_date==lesson['lesson_date']:
            return RedirectResponse('/history?notice=same',status_code=303)
        # Не объединяем два занятия одной группы автоматически: возможна потеря данных.
        duplicate=conn.execute(text('SELECT id FROM lessons WHERE group_id=:gid AND lesson_date=:day AND id<>:id LIMIT 1'),
          {'gid':lesson['group_id'],'day':new_date,'id':lesson_id}).scalar()
        if duplicate: raise HTTPException(409,'На новую дату уже есть занятие этой группы. Выбери другую дату.')
        # Нельзя перенести пропуск после даты уже выполненной отработки.
        conflict=conn.execute(text('''SELECT 1 FROM lesson_entries e JOIN makeup_sessions m ON m.missed_entry_id=e.id
          WHERE e.lesson_id=:lid AND m.actual_date<:day LIMIT 1'''),{'lid':lesson_id,'day':new_date}).scalar()
        if conflict: raise HTTPException(409,'Нельзя перенести пропуск позже даты его отработки')
        conn.execute(text('UPDATE lessons SET lesson_date=:day WHERE id=:id'),{'day':new_date,'id':lesson_id})
    return RedirectResponse('/history?notice=moved',status_code=303)

@app.post('/history/entries/{entry_id}/delete')
def history_delete_entry(entry_id:int):
    with engine.begin() as conn:
        entry=conn.execute(text('SELECT id,lesson_id FROM lesson_entries WHERE id=:id FOR UPDATE'),{'id':entry_id}).mappings().first()
        if not entry: raise HTTPException(404,'Запись не найдена')
        has_makeups=conn.execute(text('SELECT 1 FROM makeup_sessions WHERE missed_entry_id=:id LIMIT 1'),{'id':entry_id}).scalar()
        if has_makeups: raise HTTPException(409,'У записи есть отработки. Сначала удали или исправь отработки.')
        conn.execute(text('DELETE FROM lesson_entries WHERE id=:id'),{'id':entry_id})
        # Пустое занятие удаляем вместе с общей программой, иначе дата будет занята.
        count=conn.execute(text('SELECT 1 FROM lesson_entries WHERE lesson_id=:lid LIMIT 1'),{'lid':entry['lesson_id']}).scalar()
        if not count:
            conn.execute(text('DELETE FROM lessons WHERE id=:lid'),{'lid':entry['lesson_id']})
    return RedirectResponse('/history?notice=deleted',status_code=303)

from statistics_routes import router as statistics_router
app.include_router(statistics_router)


from auth import install_auth
install_auth(app, templates)
