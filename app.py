from flask import Flask, render_template, request, redirect, url_for, flash, session, g
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from models import db, User, Book, Rental, Purchase, Notification
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import os

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
app.config['SECRET_KEY'] = 'supersecret'
db.init_app(app)

login_manager = LoginManager(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def init_db():
    db.create_all()
    if not User.query.filter_by(username='admin').first():
        db.session.add(User(username='admin', password=generate_password_hash('admin'), is_admin=True))
    if not Book.query.first():
        db.session.add(Book(title='Мастер и Маргарита', author='М.Булгаков', category='Роман', year=1967, price=200, status='available', available=True))
        db.session.add(Book(title='1984', author='Д.Оруэлл', category='Антиутопия', year=1949, price=150, status='available', available=True))
        db.session.add(Book(title='Три товарища', author='Э.М.Ремарк', category='Роман', year=1936, price=170, status='available', available=True))
    db.session.commit()

# ---- УВЕДОМЛЕНИЯ ----
@app.before_request
def check_rental_notifications():
    g.notifications = []
    if current_user.is_authenticated:
        now = datetime.now()
        # автоматические уведомления о конце аренды
        rentals = Rental.query.filter_by(user_id=current_user.id).all()
        for rent in rentals:
            days_left = (rent.end_date - now).days
            if 0 <= days_left <= 3:
                g.notifications.append(
                    f"Ваша аренда книги «{rent.book.title}» заканчивается {rent.end_date.strftime('%d.%m.%Y')}. Осталось {days_left} дн."
                )
            if days_left < 0:
                g.notifications.append(
                    f"Срок аренды книги «{rent.book.title}» истёк {rent.end_date.strftime('%d.%m.%Y')}!"
                )
        # пользовательские уведомления от админа
        notifs = Notification.query.filter_by(user_id=current_user.id, is_read=False).all()
        for n in notifs:
            g.notifications.append(n.message)
            n.is_read = True
        db.session.commit()

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if User.query.filter_by(username=username).first():
            flash('Пользователь с таким именем уже существует')
            return redirect(url_for('register'))
        user = User(username=username, password=generate_password_hash(password), is_admin=False)
        db.session.add(user)
        db.session.commit()
        flash('Регистрация успешна! Войдите в систему.')
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form['username']).first()
        if user and check_password_hash(user.password, request.form['password']):
            login_user(user)
            return redirect(url_for('index'))
        else:
            flash('Неверный логин или пароль')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

@app.route('/')
def index():
    query = Book.query
    q = request.args.get('q')
    if q:
        query = query.filter(
            db.or_(
                Book.title.ilike(f'%{q}%'),
                Book.author.ilike(f'%{q}%')
            )
        )
    category = request.args.get('category')
    if category:
        query = query.filter_by(category=category)
    author = request.args.get('author')
    if author:
        query = query.filter_by(author=author)
    year = request.args.get('year')
    if year:
        query = query.filter_by(year=int(year))
    books = query.all()
    categories = sorted({b.category for b in Book.query.all()})
    years = sorted({b.year for b in Book.query.all()})
    authors = sorted({b.author for b in Book.query.all()})
    return render_template('index.html', books=books, categories=categories, years=years, authors=authors)

@app.route('/book/<int:book_id>')
def book_detail(book_id):
    book = Book.query.get_or_404(book_id)
    return render_template('book_detail.html', book=book)

def get_cart():
    return session.get('cart', [])

@app.route('/cart/add/<int:book_id>')
@login_required
def add_to_cart(book_id):
    book = Book.query.get_or_404(book_id)
    if not book.available:
        flash('Книга недоступна для заказа')
        return redirect(url_for('index'))
    cart = get_cart()
    if book_id not in cart:
        cart.append(book_id)
        session['cart'] = cart
        flash('Книга добавлена в корзину')
    else:
        flash('Книга уже в корзине')
    return redirect(request.referrer or url_for('index'))

@app.route('/cart/remove/<int:book_id>')
@login_required
def remove_from_cart(book_id):
    cart = get_cart()
    if book_id in cart:
        cart.remove(book_id)
        session['cart'] = cart
        flash('Книга удалена из корзины')
    return redirect(url_for('cart'))

@app.route('/cart', methods=['GET', 'POST'])
@login_required
def cart():
    cart = get_cart()
    books = Book.query.filter(Book.id.in_(cart)).all() if cart else []
    if request.method == 'POST':
        action = request.form.get('action')
        duration = request.form.get('duration') or '2w'
        for book in books:
            if not book.available:
                continue
            if action == 'buy':
                purchase = Purchase(user_id=current_user.id, book_id=book.id, price=book.price, purchase_date=datetime.now())
                db.session.add(purchase)
                book.status = 'sold'
                book.available = False
            elif action == 'rent':
                end_date = datetime.now()
                if duration == '2w':
                    end_date += timedelta(weeks=2)
                elif duration == '1m':
                    end_date += timedelta(days=30)
                elif duration == '3m':
                    end_date += timedelta(days=90)
                rental = Rental(user_id=current_user.id, book_id=book.id,
                                start_date=datetime.now(), end_date=end_date, duration=duration)
                db.session.add(rental)
                book.status = 'rented'
                book.available = False
        db.session.commit()
        session['cart'] = []
        flash('Заказ оформлен!')
        return redirect(url_for('user_orders'))
    return render_template('cart.html', books=books)

@app.route('/book/<int:book_id>/buy', methods=['POST'])
@login_required
def buy_book(book_id):
    book = Book.query.get_or_404(book_id)
    if not book.available:
        flash("Книга недоступна для покупки")
        return redirect(url_for('book_detail', book_id=book.id))
    purchase = Purchase(user_id=current_user.id, book_id=book.id, price=book.price, purchase_date=datetime.now())
    db.session.add(purchase)
    book.status = 'sold'
    book.available = False
    db.session.commit()
    flash('Вы купили книгу!')
    return redirect(url_for('user_orders'))

@app.route('/book/<int:book_id>/rent', methods=['POST'])
@login_required
def rent_book(book_id):
    book = Book.query.get_or_404(book_id)
    if not book.available:
        flash("Книга недоступна для аренды")
        return redirect(url_for('book_detail', book_id=book.id))
    duration = request.form.get('duration')
    end_date = datetime.now()
    if duration == '2w':
        end_date += timedelta(weeks=2)
    elif duration == '1m':
        end_date += timedelta(days=30)
    elif duration == '3m':
        end_date += timedelta(days=90)
    rental = Rental(user_id=current_user.id, book_id=book.id,
                    start_date=datetime.now(), end_date=end_date, duration=duration)
    db.session.add(rental)
    book.status = 'rented'
    book.available = False
    db.session.commit()
    flash('Вы арендовали книгу!')
    return redirect(url_for('user_orders'))

@app.route('/orders')
@login_required
def user_orders():
    bought = Purchase.query.filter_by(user_id=current_user.id).all()
    rentals = Rental.query.filter_by(user_id=current_user.id).all()
    return render_template('orders.html', bought=bought, rentals=rentals)

@app.route('/admin')
@login_required
def admin():
    if not current_user.is_admin:
        return "Доступ запрещён", 403
    books = Book.query.all()
    return render_template('admin.html', books=books)

@app.route('/admin/add', methods=['POST'])
@login_required
def add_book():
    if not current_user.is_admin:
        return "Forbidden", 403
    title = request.form['title']
    author = request.form['author']
    category = request.form['category']
    year = int(request.form['year'])
    price = float(request.form['price'])
    new_book = Book(
        title=title,
        author=author,
        category=category,
        year=year,
        price=price,
        status='available',
        available=True
    )
    db.session.add(new_book)
    db.session.commit()
    flash('Книга добавлена')
    return redirect(url_for('admin'))

@app.route('/admin/edit/<int:book_id>', methods=['POST'])
@login_required
def edit_book(book_id):
    if not current_user.is_admin:
        return "Forbidden", 403
    book = Book.query.get_or_404(book_id)
    book.title = request.form['title']
    book.author = request.form['author']
    book.category = request.form['category']
    book.year = int(request.form['year'])
    book.price = float(request.form['price'])
    book.status = request.form['status']
    book.available = 'available' in request.form or 'on' in request.form
    db.session.commit()
    flash('Книга обновлена')
    return redirect(url_for('admin'))

@app.route('/admin/delete/<int:book_id>', methods=['POST'])
@login_required
def delete_book(book_id):
    if not current_user.is_admin:
        return "Forbidden", 403
    book = Book.query.get_or_404(book_id)
    db.session.delete(book)
    db.session.commit()
    flash('Книга удалена')
    return redirect(url_for('admin'))

# ---- Тестовая кнопка для админа ----
@app.route('/admin/notify/<int:book_id>', methods=['POST'])
@login_required
def admin_notify(book_id):
    if not current_user.is_admin:
        return "Forbidden", 403
    book = Book.query.get_or_404(book_id)
    now = datetime.now()
    rentals = Rental.query.filter_by(book_id=book.id).filter(Rental.end_date > now).all()
    if rentals:
        for rent in rentals:
            for _ in range(1):
                notif = Notification(
                    user_id=rent.user_id,
                    message=f'Админ напоминает: Ваша аренда книги "{book.title}" заканчивается {rent.end_date.strftime("%d.%m.%Y")}',
                    is_read=False,
                    created_at=now
                )
                db.session.add(notif)
        db.session.commit()
        flash("Уведомления отправлены арендующим пользователям!")
    else:
        flash("Никто сейчас не арендует эту книгу.")
    return redirect(url_for('book_detail', book_id=book.id))

if __name__ == '__main__':
    with app.app_context():
        if not os.path.exists('database.db'):
            init_db()
        else:
            db.create_all()
    app.run(debug=True)
