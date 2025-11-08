from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    login_required, current_user
)
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import func
import os

# --- Configuration ---
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'a_very_secure_default_key_for_dev')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///flix.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message_category = "error" 
# Use Bootstrap flash categories for better styling
app.config['BOOTSTRAP_MSG_CATEGORY'] = {
    'success': 'success', 
    'error': 'danger', 
    'info': 'primary'
}

# ---------- Models ----------
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    
    # Relationships for new features
    watchlist = db.relationship('WatchlistItem', backref='user', lazy='dynamic', cascade="all, delete-orphan")
    reviews = db.relationship('Review', backref='author', lazy='dynamic', cascade="all, delete-orphan")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f'<User {self.username}>'


class Movie(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    synopsis = db.Column(db.Text)
    poster = db.Column(db.String(300))
    year = db.Column(db.Integer)
    genre = db.Column(db.String(50))
    duration = db.Column(db.String(20))

    # Relationships for new features
    watchlist_items = db.relationship('WatchlistItem', backref='movie', lazy='dynamic', cascade="all, delete-orphan")
    reviews = db.relationship('Review', backref='movie', lazy='dynamic', cascade="all, delete-orphan")

    def __repr__(self):
        return f'<Movie {self.title}>'

class WatchlistItem(db.Model):
    id = db.Column(db.Integer, primary_key=True) 
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    movie_id = db.Column(db.Integer, db.ForeignKey('movie.id'), nullable=False)
    
    # Ensure a user can only add a movie once to their watchlist
    __table_args__ = (db.UniqueConstraint('user_id', 'movie_id', name='_user_movie_uc'),)

class Review(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.Text, nullable=False)
    rating = db.Column(db.Integer, nullable=False) # 1 to 5
    timestamp = db.Column(db.DateTime, index=True, default=db.func.current_timestamp())
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    movie_id = db.Column(db.Integer, db.ForeignKey('movie.id'), nullable=False)
    
    # Ensure a user can only review a movie once (optional constraint)
    __table_args__ = (db.UniqueConstraint('user_id', 'movie_id', name='_user_movie_review_uc'),)


@login_manager.user_loader
def load_user(user_id):
    """Callback for flask-login to load a user by ID."""
    return db.session.get(User, int(user_id))


# ---------- Routes ----------
@app.route('/')
def index():
    """Renders the main homepage with movie listings and genre filter data."""
    # Get a list of all unique genres for the filter dropdown
    genres = db.session.query(Movie.genre).distinct().order_by(Movie.genre).all()
    # Flatten the list of single-item tuples
    genres = [g[0] for g in genres if g[0]] 
    return render_template('index.html', genres=genres)


@app.route('/api/movies')
def api_movies():
    """API endpoint for fetching movie cards (used for infinite scroll/search/filter)."""
    q = request.args.get('q', '')
    genre = request.args.get('genre', '')
    page = int(request.args.get('page', 1))
    per_page = 12
    query = db.session.query(Movie)

    if q:
        # Case-insensitive search on movie titles
        like = f"%{q}%"
        query = query.filter(Movie.title.ilike(like))

    if genre:
        # Filter by selected genre
        query = query.filter(Movie.genre == genre)

    # Apply pagination offset and limit
    items = query.order_by(Movie.year.desc()).offset((page - 1) * per_page).limit(per_page).all()
    
    # Renders the HTML snippet for the movies found
    html = render_template('moviecard.html', items=items) 
    return jsonify({'html': html})


@app.route('/movie/<int:movie_id>')
def movie_detail(movie_id):
    """Renders the detail page for a specific movie."""
    movie = db.session.get(Movie, movie_id)
    if not movie:
        flash("Movie not found.", "error")
        return redirect(url_for('index'))

    # Calculate average rating
    avg_rating = db.session.query(func.avg(Review.rating)).filter_by(movie_id=movie_id).scalar()
    avg_rating = round(avg_rating, 1) if avg_rating else 'N/A'
    
    # Fetch all reviews for the movie
    reviews = db.session.query(Review).filter_by(movie_id=movie_id).order_by(Review.timestamp.desc()).all()
    
    is_on_watchlist = False
    if current_user.is_authenticated:
        # Check if movie is on the current user's watchlist
        is_on_watchlist = db.session.query(WatchlistItem).filter_by(user_id=current_user.id, movie_id=movie_id).first() is not None

    return render_template(
        'movie_detail.html', 
        movie=movie, 
        is_on_watchlist=is_on_watchlist, 
        avg_rating=avg_rating, 
        reviews=reviews
    )


@app.route('/toggle_watchlist/<int:movie_id>', methods=['POST'])
@login_required
def toggle_watchlist(movie_id):
    """Toggles the movie's presence on the current user's watchlist."""
    movie = db.session.get(Movie, movie_id)
    if not movie:
        flash("Movie not found.", "error")
        return redirect(url_for('index'))

    watchlist_item = db.session.query(WatchlistItem).filter_by(user_id=current_user.id, movie_id=movie_id).first()

    if watchlist_item:
        # Remove from watchlist
        db.session.delete(watchlist_item)
        flash(f'"{movie.title}" removed from your watchlist.', 'info')
    else:
        # Add to watchlist
        new_item = WatchlistItem(user_id=current_user.id, movie_id=movie_id)
        db.session.add(new_item)
        flash(f'"{movie.title}" added to your watchlist!', 'success')
    
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("An error occurred while updating the watchlist.", 'error')
        
    # Redirect back to the page the user came from
    return redirect(request.referrer or url_for('movie_detail', movie_id=movie_id))


@app.route('/watchlist')
@login_required
def watchlist():
    """Renders the current user's watchlist."""
    # Fetch WatchlistItem objects for the current user, ordered by most recently added (ID)
    watchlist_items = db.session.query(WatchlistItem).filter_by(user_id=current_user.id).order_by(WatchlistItem.id.desc()).all()
    
    # Extract the Movie objects
    movies = [item.movie for item in watchlist_items if item.movie] 
    
    return render_template('watchlist.html', movies=movies)


@app.route('/submit_review/<int:movie_id>', methods=['POST'])
@login_required
def submit_review(movie_id):
    """Handles the submission of a user review for a movie."""
    movie = db.session.get(Movie, movie_id)
    if not movie:
        flash("Movie not found.", "error")
        return redirect(url_for('index'))

    rating = request.form.get('rating')
    text = request.form.get('text')
    
    if not rating or not text:
        flash("Rating and review text are required.", 'error')
        return redirect(url_for('movie_detail', movie_id=movie_id))

    try:
        rating = int(rating)
        if not 1 <= rating <= 5:
             flash("Rating must be between 1 and 5.", 'error')
             return redirect(url_for('movie_detail', movie_id=movie_id))

        # Check if the user has already reviewed this movie
        existing_review = db.session.query(Review).filter_by(user_id=current_user.id, movie_id=movie_id).first()
        
        if existing_review:
            # Update existing review
            existing_review.rating = rating
            existing_review.text = text
            existing_review.timestamp = db.func.current_timestamp()
            flash('Your review has been updated!', 'success')
        else:
            # Create new review
            new_review = Review(
                user_id=current_user.id,
                movie_id=movie_id,
                rating=rating,
                text=text
            )
            db.session.add(new_review)
            flash('Your review has been posted!', 'success')

        db.session.commit()
    except ValueError:
        flash("Invalid rating submitted.", 'error')
        db.session.rollback()
    except Exception as e:
        # This catches the UniqueConstraint violation if it somehow bypasses the check, and other DB errors
        flash(f"An error occurred while posting the review: You may have already reviewed this movie.", 'error')
        db.session.rollback()

    return redirect(url_for('movie_detail', movie_id=movie_id))


@app.route('/login', methods=['GET', 'POST'])
def login():
    """Handles user login."""
    if current_user.is_authenticated:
        return redirect(url_for('index'))
        
    if request.method == 'POST':
        identity = request.form['identity']
        password = request.form['password']
        user = db.session.query(User).filter(
            (User.username == identity) | (User.email == identity)
        ).first()
        
        if user and user.check_password(password):
            login_user(user)
            flash(f'Welcome back, {user.username}!', 'success')
            next_page = request.args.get('next')
            return redirect(next_page or url_for('index'))
        
        flash('Invalid username/email or password.', 'error')
            
    return render_template('login.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    """Handles new user registration."""
    if current_user.is_authenticated:
        return redirect(url_for('index'))
        
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']
        
        if db.session.query(User).filter((User.username == username) | (User.email == email)).first():
            flash('Account with this username or email already exists.', 'error')
            return redirect(url_for('register'))
            
        try:
            user = User(username=username, email=email)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            
            login_user(user)
            flash(f'Account created successfully. Welcome, {user.username}!', 'success')
            return redirect(url_for('index'))
        except Exception as e:
            db.session.rollback()
            flash(f'An error occurred during registration: {str(e)}', 'error')
            
    return render_template('register.html')


@app.route('/logout')
@login_required
def logout():
    """Logs out the current user."""
    logout_user()
    flash('You have been logged out.', 'success')
    return redirect(url_for('index'))


@app.route('/admin/add', methods=['GET', 'POST'])
@login_required
def add_movie():
    """Allows admin users to add new movies to the database."""
    # Simple check for admin: user with 'admin' username. In a real app, use roles.
    if current_user.username != 'admin':
        flash('Access denied. Only the admin can add movies.', 'error')
        return redirect(url_for('index'))
        
    if request.method == 'POST':
        try:
            title = request.form['title']
            genre = request.form['genre']
            poster = request.form['poster']
            year = int(request.form['year']) 
            duration = request.form['duration']
            synopsis = request.form['synopsis']

            m = Movie(
                title=title, genre=genre, poster=poster,
                year=year, duration=duration, synopsis=synopsis
            )
            db.session.add(m)
            db.session.commit()
            flash(f'Movie "{title}" added successfully!', 'success')
            return redirect(url_for('movie_detail', movie_id=m.id))
        except ValueError:
            flash("Invalid input for Year. Please enter a valid number.", "error")
        except Exception as e:
            db.session.rollback()
            flash(f"An error occurred while adding the movie: {str(e)}", "error")

    return render_template('add_movie.html')


@app.route('/admin/edit/<int:movie_id>', methods=['GET', 'POST'])
@login_required
def edit_movie(movie_id):
    """Allows admin users to edit an existing movie."""
    # Simple check for admin: user with 'admin' username. In a real app, use roles.
    if current_user.username != 'admin':
        flash('Access denied. Only the admin can edit movies.', 'error')
        return redirect(url_for('movie_detail', movie_id=movie_id))
        
    movie = db.session.get(Movie, movie_id)
    if not movie:
        flash("Movie not found.", "error")
        return redirect(url_for('index'))

    if request.method == 'POST':
        try:
            movie.title = request.form['title']
            movie.genre = request.form['genre']
            movie.poster = request.form['poster']
            movie.year = int(request.form['year']) 
            movie.duration = request.form['duration']
            movie.synopsis = request.form['synopsis']

            db.session.commit()
            flash(f'Movie "{movie.title}" updated successfully!', 'success')
            return redirect(url_for('movie_detail', movie_id=movie.id))
        except ValueError:
            flash("Invalid input for Year. Please enter a valid number.", "error")
            db.session.rollback()
        except Exception as e:
            db.session.rollback()
            flash(f"An error occurred while updating the movie: {str(e)}", "error")

    # Reuses add_movie.html template, passing the movie object for pre-filling
    return render_template('add_movie.html', movie=movie)


# ---------- Setup and Seeding ----------
def seed_data():
    """Initializes the database and populates it with sample data if empty."""
    # --- FIX: Drop and create all tables to apply new schema (timestamp column) ---
    # This will wipe existing data.
    print("WARNING: Dropping all database tables to apply new schema changes.")
    db.drop_all()
    db.create_all()
    # -----------------------------------------------------------------------------
    
    # Create default admin user
    if not db.session.query(User).filter_by(username='admin').first():
        admin_user = User(username='admin', email='admin@flix.com')
        admin_user.set_password('password123') 
        db.session.add(admin_user)
        print("Created default admin user: 'admin' / 'password123'")

    # Seed more movies if the count is low for a good display
    if db.session.query(Movie).count() < 10:
        base_url = "https://placehold.co/300x450"
        
        sample = [
{
             "title": "Interstellar",
             "genre": "Sci-Fi",
             # Updated Poster URL
             "poster": "https://cdna.artstation.com/p/assets/images/images/063/201/604/large/ian-papa-inter.jpg?1684955718",
             "year": 2014,
             "duration": "2h 49m",
             "synopsis": "A team of explorers travels through a wormhole in space in an attempt to ensure humanity's survival."
        },
        {
             "title": "Dune: Part One",
             "genre": "Sci-Fi",
             # Updated Poster URL
             "poster": "https://th.bing.com/th/id/OIP.H8940g-GeXdBWSXjUjh2swHaK8?w=205&h=303&c=7&r=0&o=7&cb=ucfimg2&dpr=1.3&pid=1.7&rm=3&ucfimg=1",
             "year": 2021,
             "duration": "2h 35m",
             "synopsis": "Paul Atreides leads nomadic tribes in a battle to control the desert planet Arrakis and its valuable spice."
        },
        {
             "title": "Harry Potter and the Sorcerer's Stone",
             "genre": "Fantasy",
             # Updated Poster URL
             "poster": "https://wallpapers.com/images/hd/harry-potter-all-characters-lvbwsigjt3yykg3n.jpg",
             "year": 2001,
             "duration": "2h 32m",
             "synopsis": "An orphaned boy discovers he is a wizard and attends a magical school where he faces his destiny."
        },
        {
             "title": "3 Idiots",
             "genre": "Comedy",
             # Updated Poster URL
             "poster": "https://m.media-amazon.com/images/M/MV5BNTkyOGVjMGEtNmQzZi00NzFlLTlhOWQtODYyMDc2ZGJmYzFhXkEyXkFqcGdeQXVyNjU0OTQ0OTY@._V1_.jpg",
             "year": 2009,
             "duration": "2h 50m",
             "synopsis": "Three engineering students navigate life, friendship, and education with a brilliant yet unconventional classmate."
        },
        {
             "title": "Life of Pi",
             "genre": "Adventure",
             # Updated Poster URL
             "poster": "https://tse1.mm.bing.net/th/id/OIP.WRQCZc_zReaLUXjSFa0z9QHaF7?cb=ucfimg2ucfimg=1&rs=1&pid=ImgDetMain&o=7&rm=3",
             "year": 2012,
             "duration": "2h 7m",
             "synopsis": "After a shipwreck, a young man survives on a lifeboat with a Bengal tiger, discovering spirituality and survival."
        },
        {
             "title": "Rio",
             "genre": "Animation",
             # Updated Poster URL
             "poster": "https://assets.puzzlefactory.com/puzzle/482/150/original.jpg",
             "year": 2011,
             "duration": "1h 36m",
             "synopsis": "A domesticated blue macaw travels to Rio de Janeiro, where he learns to fly and embrace adventure."
        },
        {
             "title": "Inception",
             "genre": "Sci-Fi",
             # Updated Poster URL
             "poster": "https://c8.alamy.com/comp/DBW2R3/inception-2010-leonardo-dicaprio-christopher-nolan-dir-016-moviestore-DBW2R3.jpg",
             "year": 2010,
             "duration": "2h 28m",
             "synopsis": "A thief who steals corporate secrets through dream-sharing technology is tasked with planting an idea into a CEO's mind."
        },
        {
             "title": "Avatar",
             "genre": "Fantasy",
             # Updated Poster URL
             "poster": "https://mlpnk72yciwc.i.optimole.com/cqhiHLc.IIZS~2ef73/w:auto/h:auto/q:75/https://bleedingcool.com/wp-content/uploads/2022/11/AVATAR_THE_WAY_OF_WATER_1SHT_DIGITAL_LOAK_sRGB_V1.jpg",
             "year": 2009,
             "duration": "2h 42m",
             "synopsis": "A paraplegic Marine is sent to the moon Pandora, where he becomes torn between following orders and protecting its world."
        },
        {
             "title": "The Dark Knight",
             "genre": "Action",
             # Updated Poster URL
             "poster": "https://v3img.voot.com/v3Storage/assets/the_dark_knight_1920x1080-1685950193383.jpg",
             "year": 2008,
             "duration": "2h 32m",
             "synopsis": "Batman faces his greatest psychological and physical test yet as he battles the Joker's chaos in Gotham."
        },
        {
             "title": "Frozen",
             "genre": "Animation",
             # Updated Poster URL
             "poster": "https://images.hdqwalls.com/wallpapers/hd-frozen.jpg",
             "year": 2013,
             "duration": "1h 42m",
             "synopsis": "When Queen Elsa's icy powers trap her kingdom in eternal winter, her sister Anna sets out to save her."
        }
        ]
        
        for m in sample:
            if not db.session.query(Movie).filter_by(title=m['title']).first():
                db.session.add(Movie(**m))
        
        # Seed a sample review for testing
        admin_user = db.session.query(User).filter_by(username='admin').first()
        dune = db.session.query(Movie).filter_by(title="Dune: Part One").first()
        if admin_user and dune and not db.session.query(Review).first():
            review = Review(
                user_id=admin_user.id,
                movie_id=dune.id,
                rating=5,
                text="A visually stunning and faithful adaptation. The sound design is incredible."
            )
            db.session.add(review)
            print("Seeded a sample review.")
        
        db.session.commit()
        print(f"Seeded {db.session.query(Movie).count()} sample movies.")


if __name__ == '__main__':
    with app.app_context():
        seed_data()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
