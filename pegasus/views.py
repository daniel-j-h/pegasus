from pegasus import app
import sqlite3
import uuid
import string
import random
from flask import request, session, g, redirect, url_for, abort, render_template, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
from itertools import islice



# all the definitions
def get_random_string(length=32):
     return ''.join(random.choice(string.ascii_letters + string.digits) for i in range(length))

def generate_csrf_token():
    if '_csrf_token' not in session:
        session['_csrf_token'] = get_random_string()
    return session['_csrf_token']

app.jinja_env.globals['csrf_token'] = generate_csrf_token

def login_user(username):
    session['logged_in'] = True
    session['username'] = username
    cur = g.db.execute('select id from users where username=?', [username]).fetchone()
    uid = cur[0]
    session['userid'] = uid # to register any info in another table where userid is a FK instead of querying every time


def is_owner(boardID, userID):
    cur = g.db.execute('select creatorID from boards where id=?', [boardID]).fetchone()[0]
    if cur == userID:
        return True
    else:
        return False

def lock_board(boardID, userID=None, userEmail=None): 
    user = userID if userID is not None else userEmail
    lock = datetime.utcnow() + timedelta(seconds=5) 
    lock_time = lock.strftime('%Y-%m-%d %H:%M:%S')
    g.db.execute('update boards set locked_until=?, locked_by=? where id=?', [lock_time, user, boardID])
    g.db.commit()

def is_authorized(boardID, wantToEdit=False):
    access = False
    isOwner = False
    accessType = None
    canEditNow = False
    if session.get('logged_in'):
        # not counting in the invitation link logic here
        uid = session['userid']
        # are they the owner?
        if is_owner(boardID, uid):
            access = True
            isOwner = True
            accessType = 'edit'
        else:
            uemail = g.db.execute('select email from users where id=?', [uid]).fetchone()[0]
            cur2 = g.db.execute('select type from invites where boardID=? and userEmail=?', [boardID, uemail]).fetchone()
            if cur2 is not None:
                access = True
                accessType = cur2[0]
        if accessType =='edit' and wantToEdit:
            # boardID must exist at this point, checked by calling functions
            lock = g.db.execute('select locked_until, locked_by from boards where id=?', [boardID]).fetchone()
            if lock is not None:
                lockedUntil = datetime.strptime(lock[0], '%Y-%m-%d %H:%M:%S')
                lockedBy = lock[1]
                if (datetime.utcnow() > lockedUntil) or (datetime.utcnow() < lockedUntil and int(lockedBy) == uid):
                    try:
                        lock_board(boardID, userID=str(uid))
                        canEditNow = True
                    except sqlite3.Error as e:
                        pass
    return {'access':access, 'isOwner':isOwner, 'accessType':accessType, 'canEditNow':canEditNow}





    
# routing (views)
@app.route('/')
def index():
    if session.get('logged_in'):
        email = g.db.execute('select email from users where id=?', [session['userid']]).fetchone()[0].lower()
        cur2 = g.db.execute('select id, title from boards where id in (select boardID from invites where userEmail=?)', [email]).fetchall()
        invitedLi = [dict(id=row[0], title=row[1]) for row in cur2]
        return render_template('show_list.html', invitedBoards=invitedLi)
    else:
        cur = g.db.execute('select username, join_date from users order by id')
        li = [dict(username=row[0], jdate=row[1]) for row in cur.fetchall()]
        return render_template('show_list.html', li=li)

@app.route('/register', methods=['GET', 'POST'])
def register_user():
    if session.get('logged_in'):
        abort(401)
    error = None
    if request.method == 'POST':
        try:
            pw = generate_password_hash(request.form['password'])
            un = request.form['username'].lower()
            em = request.form['email'].lower()
            g.db.execute('insert into users (username, password, email, name) values (?, ?, ?, ?)', [un, pw, em, request.form['name']])
            g.db.commit()
            login_user(un)
            return redirect(url_for('index'))
        except sqlite3.IntegrityError as e:
            if e.args[0][32:] == 'email':
                error = 'Email'
            elif e.args[0][32:] == 'username':
                error = 'Username'
            error = error + ' already in use.'
    return render_template('register.html', error=error)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('logged_in'):
        abort(401)
    error = None
    if request.method == 'POST':
        cur = g.db.execute('select username, password from users where username=?', [request.form['username'].lower()])
        cur_res = cur.fetchone()
        if cur_res is None:
            error = 'Invalid username'
        else:
            username = cur_res[0]
            pw = cur_res[1]
            if check_password_hash(pw, request.form['password']) == False: # ouch
                error = 'Invalid password'
            else:
                login_user(username)
                flash('Hey there!', 'info')
                return redirect(url_for('index'))
    return render_template('login.html', error=error)

@app.route('/profile')
def show_profile():
    if not session.get('logged_in'):
        abort(401)
    uid = session.get('userid')
    cur = g.db.execute('select name, email from users where id=?', [uid]).fetchone()
    cur2 = g.db.execute('select id, title from boards where creatorID=?', [uid]).fetchall()
    boards = [dict(id=row[0], title=row[1]) for row in cur2]
    return render_template('profile.html', name=cur[0], email=cur[1], boards=boards)

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    session.pop('username', None)
    session.pop('userid', None)
    flash('You go bye bye :(', 'warning')
    return redirect(url_for('index')) # always going there..

@app.route('/new-board', methods=['GET', 'POST'])
def create_board():
    if not session.get('logged_in'):
        abort(401)
    error = None
    if request.method == 'POST':
        try:
            uid = session.get('userid')
            title = request.form['title']
            done = datetime.utcnow() + timedelta(days=1)
            done_at = done.strftime('%Y-%m-%d %H:%M:%S')
            locked_by = str(uid)
            cur = g.db.cursor()
            cur.execute('insert into boards (creatorID, title, done_at, locked_by) values (?, ?, ?, ?)', [uid, title, done_at, uid])
            g.db.commit()
            boardID = cur.lastrowid
            cur.close()
            return redirect(url_for('show_board', boardID=boardID))
        except sqlite3.Error as e:
            error = 'An error occured: ' + e.args[0]
    return render_template('new-board.html', error=error)

@app.route('/board/<boardID>')
def show_board(boardID):
    # first, check if there's even a board
    curB = g.db.execute('select title, created_at, done_at from boards where id=?', [boardID]).fetchone()
    if curB is None:
        abort(404)
    else:
        invite = request.args.get('invite') # ?invite=INVITE_ID
        auth = is_authorized(boardID)
        can_participate = False
        done_at = datetime.strptime(curB[2], '%Y-%m-%d %H:%M:%S')
        isDone = False
        if(done_at < datetime.utcnow()):
            isDone = True
        if auth['access']: # don't care if there's an invite string as long as you have access while logged in
            if auth['accessType'] == 'edit':
                can_participate = True
            return render_template('show-board.html', canEdit=can_participate, isDone=isDone, title=curB[0], created_at=curB[1], done_at=curB[2], isOwner=auth['isOwner'], boardID=boardID)
        elif invite is not None:
            cur = g.db.execute('select userEmail, type from invites where id=? and boardID=?', [invite, boardID]).fetchone()
            if cur is None:
                abort(401)
            else:
                if cur[1] == 'edit':
                    can_participate = True
                return render_template('show-board.html', canEdit=can_participate, isDone=isDone, title=curB[0], created_at=curB[1], done_at=curB[2], email=cur[0], boardID=boardID)
        else:
            abort(401)

@app.route('/_deleteBoard', methods=['POST'])
def delete_board():
    if not session.get('logged_in'):
        abort(401)
    else:
        error = 'None'
        new_token = generate_csrf_token()
        bid = int(request.form['boardID'])
        try:
            g.db.execute('delete from boards where id=? and creatorID=?', [bid, session['userid']])
            g.db.commit()
        except sqlite3.Error as e:
            error = e.args[0]
        if(error!='None'):
            flash(error)
        return redirect(url_for('show_profile'))

@app.route('/_removeSelf', methods=['POST'])
def remove_self():
    if not session.get('logged_in'):
        abort(401)
    else:
        error = 'None'
        person = session['userid']
        bid = int(request.form['boardID'])
        try:
            cur = g.db.execute('select email from users where id=?', [person]).fetchone()
            if cur is None:
                abort(400)
            else:
                email = cur[0].lower()
                g.db.execute('delete from invites where boardID=? and userEmail=?', [bid, email])
                g.db.commit()
        except sqlite3.Error as e:
            error = e.args[0]
        if(error=='None'):
            flash('Successfully removed you from the board.')
        else:
            flash(error)
        return redirect(url_for('index'))



# AJAX functions
## GET
@app.route('/_validateUsername')
def valUsername():
    un = request.args.get('username', 0, type=str)
    cur = g.db.execute('select id from users where username=?', [un.lower()]).fetchone()
    if cur is None:
        return jsonify(available='true')
    else:
        return jsonify(available='false')

@app.route('/_validateEmail')
def valEmail():
    em = request.args.get('email', 0, type=str)
    cur = g.db.execute('select id from users where email=?', [em.lower()]).fetchone()
    if cur is None:
        return jsonify(available='true')
    else:
        return jsonify(available='false')


## POST
@app.route('/_editBoard', methods=['POST'])
def edit_board():
    if not session.get('logged_in'):
        abort(401)
    else: # is logged in
        curBoard = g.db.execute('select title from boards where id=?', [int(request.form['boardID'])]).fetchone()
        if curBoard is None:
            abort(404)
        error = 'None'
        new_token = generate_csrf_token()
        try:
            g.db.execute('update boards set title=? where id=? and creatorID=?', [request.form['title'], int(request.form['boardID']), session['userid']])
            g.db.commit()
        except sqlite3.Error as e:
            error = e.args[0]
        return jsonify(error=error, token=new_token) # and new CSRF token to be used again

@app.route('/_editProfile', methods=['POST'])
def edit_profile():
    if not session.get('logged_in'):
        abort(401)
    else:
        error = 'None'
        new_token = generate_csrf_token()
        cur = g.db.execute('select name, email, username from users where id=?', [session['userid']]).fetchone()
        old_name = cur[0]
        old_email = cur[1]
        old_username = cur[2]
        em = request.form['email'].lower()
        un = request.form['username'].lower()
        name = request.form['name']
        # first, check availability
        okay = True
        cur1 = g.db.execute('select id from users where email=?', [em]).fetchone()
        cur2 = g.db.execute('select id from users where username=?', [un]).fetchone()
        if cur1 is not None:
            if cur1[0] != session['userid']:
                okay = False
                error = 'Email is not available.'
        if cur2 is not None:
            if cur2[0] != session['userid']:
                okay = False
                if error == 'None':
                    error = 'Username is not available.'
                else:
                    error+='Username is not available.'
        if okay:
            if old_name != name or old_email != em or old_username != un: # only proceed if any changes were made
                try:
                    old_em = g.db.execute('select email from users where id=?', [session['userid']]).fetchone()[0].lower()
                    g.db.execute('update users set name=?, email=?, username=? where id=?', [name, em, un, session['userid']])
                    session['username'] = un;
                    g.db.execute('update invites set userEmail=? where userEmail=?', [em, old_em])
                    g.db.commit()
                except sqlite3.Error as e:
                    error = e.args[0]
        return jsonify(error=error, token=new_token)

@app.route('/_changePassword', methods=['POST'])
def change_password(): 
    if not session.get('logged_in'):
        abort(401)
    else:
        error = 'None'
        new_token = generate_csrf_token()
        password = generate_password_hash(request.form['password'])
        pw = g.db.execute('select password from users where id=?', [session['userid']]).fetchone()[0]
        if not check_password_hash(pw, request.form['old-password']):
            error = 'Old password you entered is incorrect.'
        else: 
            try:
                g.db.execute('update users set password=? where id=?', [password, session['userid']])
                g.db.commit()
            except sqlite3.Error as e:
                error = e.args[0]
        return jsonify(error=error, token=new_token)

@app.route('/_markDone', methods=['POST'])
def mark_done():
    if not session.get('logged_in'):
        abort(401)
    else:
        error = 'None'
        new_token = generate_csrf_token()
        done = datetime.utcnow()
        done_at = done.strftime('%Y-%m-%d %H:%M:%S')
        bid = int(request.form['boardID'])
        old_done_at = datetime.strptime(g.db.execute('select done_at from boards where id=?', [bid]).fetchone()[0], '%Y-%m-%d %H:%M:%S')
        if old_done_at < done:
            abort(400)
        try:
            g.db.execute('update boards set done_at=? where id=? and creatorID=?', [done_at, bid, session['userid']])
            g.db.commit()
        except sqlite3.Error as e:
            error = e.args[0]
        return jsonify(error=error, token=new_token)


@app.route('/_inviteUser', methods=['POST'])
def invite_user():
    if not session.get('logged_in'):
        abort(401)
    em = request.form['email'].lower()
    ty = request.form['type'] # view or edit
    b_id = int(request.form['boardID'])
    user = session['userid']
    inviteID = uuid.uuid4().hex
    error = 'None'
    successful='false'
    if is_owner(b_id, user):
        try:
            g.db.execute('insert into invites (id, userEmail, boardID, type) values (?, ?, ?, ?)', [inviteID, em, b_id, ty])
            g.db.commit()
            successful = 'true'
        except sqlite3.IntegrityError as e:
            error = 'This email has already been invited to this board.'
        except sqlite3.Error as e: # for debugging
            error = e.args[0]
        finally:
            new_token = generate_csrf_token()
    return jsonify(successful=successful, error=error, token=new_token)

@app.route('/_editInvite', methods=['POST'])
def edit_invite():
    bid = int(request.form['boardID'])
    em = request.form['email']
    old_type = request.form['inviteType']
    new_token = generate_csrf_token()
    error = 'None'
    if not session.get('logged_in') or not is_owner(bid, session['userid']):
        abort(401)
    else:
        if old_type=='edit':
            new_type = 'view'
        elif old_type=='view':
            new_type = 'edit'
        else:
            abort(400)
        try:
            g.db.execute('update invites set type=? where boardID=? and userEmail=?', [new_type, bid, em])
            g.db.commit()
        except sqlite3.Error as e:
            error = e.args[0]
        return jsonify(error=error, token=new_token)

## API (GET/POST)
@app.route('/api/board/<boardID>/components/get', methods=['GET'])
def get_components(boardID):
    bid = int(boardID)
    curBoard = g.db.execute('select locked_until, locked_by from boards where id=?', [bid]).fetchone()
    if curBoard is None:
        abort(404)
    else:
        inv = request.args.get('invite', 0, str)
        if inv != '-1' and not session.get('logged_in'):
            curInvite = g.db.execute('select userEmail from invites where id=?', [inv]).fetchone()
            if curInvite is None:
                abort(401)
            else:
                who = curInvite[0]
        elif session.get('logged_in'):
            auth = is_authorized(bid)
            if not auth['access']:
                abort(401)
            else:
                who = str(session['userid'])
        else:
            abort(401)
        lastClientGot = request.args.get('lastModified', 0, str)
        lock_until = datetime.strptime(curBoard[0], '%Y-%m-%d %H:%M:%S')
        lock_by = curBoard[1]
        LOCKED = False
        if datetime.utcnow() < lock_until and lock_by != who:
            LOCKED = True
        # get list
        try:
            curList = g.db.execute('select id, content, userID, userEmail, created_at, last_modified_at, last_modified_by, type, position, deleted from board_content where boardID=? and last_modified_at > ? order by created_at', [bid, lastClientGot]).fetchall()
            if len(curList) > 0:
                messages = [dict(id=row[0], content=row[1], userID=row[2], userEmail=row[3], created_at=row[4], last_modified_at=row[5], last_modified_by=row[6], type=row[7], position=row[8], deleted=row[9]) for row in curList]
                return jsonify(messages=messages, locked=LOCKED, lockedBy=lock_by)
            else:
                error = 'Nothing new.'
                return jsonify(error=error, locked=LOCKED, lockedBy=lock_by)
        except sqlite3.Error as e:
            error = e.args[0]
            return jsonify(error=error, locked=LOCKED, lockedBy=lock_by)

@app.route('/api/board/<boardID>/components/post', methods=['POST'])
def post_components(boardID):
    bid = int(boardID)
    new_token = generate_csrf_token()
    msg = request.form['message']
    ty = request.form['content-type']
    position = request.form['position']
    error = 'None'
    componentID = None
    wantEdit = False if ty == 'chat' else True
    curBoard = g.db.execute('select done_at, locked_until, locked_by from boards where id=?', [bid]).fetchone()
    if curBoard is None:
        abort(404)
    done_at = datetime.strptime(curBoard[0], '%Y-%m-%d %H:%M:%S')
    inv = request.form['invite']
    if inv != '-1' and not session.get('logged_in'):
        curInvite = g.db.execute('select type, userEmail from invites where id=? and boardID=?', [inv, bid]).fetchone()
        if curInvite is None:
            abort(401)
        else:
            who = curInvite[1]
    elif session.get('logged_in'):
        auth = is_authorized(bid, wantToEdit=wantEdit)
        if not auth['access'] and auth['accessType'] == 'edit':
            abort(401)
        who = session['userid']
    if(done_at > datetime.utcnow()):
        if len(msg)>=1:
            if session.get('logged_in'):
                if (ty != 'chat' and auth['canEditNow']) or (ty == 'chat'):
                    try:
                        cursor = g.db.cursor()
                        cursor.execute('insert into board_content (boardID, userID, content, type, position, last_modified_at, last_modified_by) values (?, ?, ?, ?, ?, ?, ?)', [bid, who, msg, ty, position, datetime.utcnow(), who])
                        g.db.commit()
                        componentID = cursor.lastrowid
                        cursor.close()
                    except sqlite3.Error as e:
                        error = e.args[0]
                else:
                    error = 'This board is locked for edit by another user.'
            elif inv != '-1' and ((curInvite[0] == 'edit' and ty != 'chat') or (ty == 'chat')):
                lockedUntil = datetime.strptime(curBoard[1],'%Y-%m-%d %H:%M:%S')
                lockedBy = curBoard[2]
                allowEdit = False
                if datetime.utcnow() > lockedUntil or (datetime.utcnow() < lockedUntil and lockedBy == who):
                    try:
                        lock_board(bid, userEmail=who)
                        allowEdit = True
                    except sqlite3.Error as e:
                        error = e.args[0]
                if allowEdit:
                    try:
                        cursor = g.db.cursor()
                        cursor.execute('insert into board_content (boardID, userEmail, content, type, position, last_modified_at, last_modified_by) values (?, ?, ?, ?, ?, ?, ?)', [bid, who, msg, ty, position, datetime.utcnow(), who])
                        g.db.commit()
                        cursor.close()
                    except sqlite3.Error as e:
                        error = e.args[0]
                else:
                    error = 'This board is locked for edit by another user.'
            else:
                error = 'Your priviliges do not allow you to post to this board.'
        else:
            error = 'Content too short.'
    else:
        error = 'This board has expired. You cannot make any changes.'
    return jsonify(error=error, token=new_token, componentID=componentID)


@app.route('/api/user/<userID>', methods=['GET'])
def get_user(userID):
    # no authentication needed, public info. A better app would only provide this info to people who have something in common with the user
    ## like they share a board. But for now, it's just public.
    error = 'None'
    username = None
    name = None
    try:
        cur = g.db.execute('select name, username from users where id=?', [int(userID)]).fetchone()
        if cur is None:
            error = 'User not found.'
        else:
            name = cur[0]
            username = cur[1]
    except sqlite3.Error as e: # just in case
        error = e.args[0]
    return jsonify(error=error, username=username, name=name)

@app.route('/api/invited/<boardID>', methods=['GET'])
def invited_users(boardID):
    error = 'None'
    invited = 'None'
    new_token = generate_csrf_token() # for the POST forms generated on the fly
    bid = int(boardID)
    curBoard = g.db.execute('select title from boards where id=?', [bid]).fetchone()
    if curBoard is None:
        abort(404)
    if not session.get('logged_in') or not is_owner(bid, session['userid']):
        abort(401)
    else:
        try:
            cur = g.db.execute('select userEmail, type from invites where boardID=? order by invite_date', [bid]).fetchall()
            if len(cur) == 0:
                error = 'No one has been invited to this board yet.'
            else:
                invited = [dict(userEmail=row[0], type=row[1]) for row in cur]
        except sqlite3.Error as e:
            error = e.args[0]
        return jsonify(error=error, invited=invited, token=new_token)

@app.route('/api/edit/board/<boardID>/component/<componentID>', methods=['POST'])
def edit_component(componentID, boardID):
    error = 'None'
    new_token = generate_csrf_token()
    bid = int(boardID)
    cid = int(componentID)
    inv = request.form['invite']
    curBoard = g.db.execute('select locked_until, locked_by from boards where id=?', [bid]).fetchone()
    if curBoard is None:
        abort(404)
    if inv != '-1' and not session.get('logged_in'): # don't care if there's an invite string as long as they're logged in
        cur = g.db.execute('select type, userEmail from invites where id=? and boardID=?', [inv, bid]).fetchone()
        if cur is None:
            abort(401)
        elif cur[0] != 'edit':
            abort(401)
        else:
            mod = cur[1]
            lockedUntil = datetime.strptime(curBoard[0], '%Y-%m-%d %H:%M:%S')
            lockedBy = curBoard[1]
            if(datetime.utcnow() > lockedUntil) or (datetime.utcnow() < lockedUntil and lockedBy == mod):
                try:
                    lock_board(bid,userEmail=mod)
                    allowEdit = True
                except sqlite3.Error as e:
                    error = e.args[0]
    elif session.get('logged_in'):
        auth = is_authorized(bid, wantToEdit=True)
        if not auth['access'] or not auth['accessType'] == 'edit' or not auth['canEditNow']:
            abort(401)
        else:
            allowEdit = True
            mod = session['userid']
    else:
        abort(401)
    # if we get this far, user has editing access
    if allowEdit:
        ty = request.form['content-type']
        nowDate = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        curDone = g.db.execute('select done_at from boards where id=?', [bid]).fetchone()
        done_at = datetime.strptime(curDone[0], '%Y-%m-%d %H:%M:%S')
        if done_at > datetime.utcnow():
            try:
                if request.form['hasMessages']=='true':
                    msg = request.form['message']
                    if len(msg)>=1:
                        g.db.execute('update board_content set content=?, last_modified_at=?, last_modified_by=? where id=? and boardID=? and type=? and deleted=?', [msg, nowDate, mod, cid, bid, ty, 'N'])
                        g.db.commit()
                    else:
                        error = 'Content too short.'
                else: # refreshing position only
                    pos = request.form['position']
                    g.db.execute('update board_content set position=?, last_modified_at=?, last_modified_by=? where id=? and boardID=? and type=? and deleted=?', [pos, nowDate, mod, cid, bid, ty, 'N'])
                    g.db.commit()
            except sqlite3.Error as e:
                error = e.args[0]
        else:
            error = 'This board has expired. You cannot make any more changes.'
    else: 
        error = 'This board is locked for edit by another user.'
    return jsonify(error=error, token=new_token)

@app.route('/api/delete/board/<boardID>/component/<componentID>', methods=['POST'])
def delete_component(boardID, componentID):
    error = 'None'
    new_token = generate_csrf_token()
    bid = int(boardID)
    cid = int(componentID)
    inv = request.form['invite']
    curBoard = g.db.execute('select locked_until, locked_by from boards where id=?', [bid]).fetchone()
    if curBoard is None:
        abort(404)
    if inv != '-1' and not session.get('logged_in'): # don't care if there's an invite string as long as they're logged in
        cur = g.db.execute('select type, userEmail from invites where id=? and boardID=?', [inv, bid]).fetchone()
        if cur is None:
            abort(401)
        elif cur[0] != 'edit':
            abort(401)
        else:
            mod = cur[1]
            lockedUntil = datetime.strptime(curBoard[0], '%Y-%m-%d %H:%M:%S')
            lockedBy = curBoard[1]
            if(datetime.utcnow() > lockedUntil) or (datetime.utcnow() < lockedUntil and lockedBy == mod):
                try:
                    lock_board(bid,userEmail=mod)
                    allowDelete = True
                except sqlite3.Error as e:
                    error = e.args[0]
    elif session.get('logged_in'):
        auth = is_authorized(bid, wantToEdit=True)
        if not auth['access'] or not auth['accessType'] == 'edit' or not auth['canEditNow']:
            abort(401)
        else:
            allowDelete = True
            mod = session['userid']
    else:
        abort(401)
    if allowDelete:
        nowDate = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        curDone = g.db.execute('select done_at from boards where id=?', [bid]).fetchone()
        done_at = datetime.strptime(curDone[0], '%Y-%m-%d %H:%M:%S')
        if done_at > datetime.utcnow():
            try:
                g.db.execute('update board_content set deleted=?, last_modified_at=?, last_modified_by=? where id=? and boardID=? and type!=?', ['Y', nowDate, mod, cid, bid, 'chat'])
                g.db.commit()
            except sqlite3.Error as e:
                error = e.args[0]
        else:
            error = 'This board has expired. You cannot make any more changes.'
    else:
        error = 'This board is locked for edit by another user.'
    return jsonify(error=error, token=new_token)



