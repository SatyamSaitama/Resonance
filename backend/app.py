

"===========================================================IMPORTS====================================================================================="

from flask import Flask, request, jsonify, redirect, url_for,session
from flask_cors import CORS
from flask_restful import Api, Resource
import os
from model import *
import re
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
from datetime import timedelta
from collections import Counter,defaultdict
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from concurrent.futures import ThreadPoolExecutor
from flask_oauthlib.client import OAuth
import os
from dotenv import load_dotenv
# Load environment variables from .env 
load_dotenv()
import openai
openai.api_key = os.getenv("OPENAI_API_KEY") 



"===========================================================FLASK APP SETUP====================================================================================="

app = Flask(__name__)

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///transcriptions.db'
app.config['SECRET_KEY'] = 'your-secret-key'
app.config['JWT_SECRET_KEY'] = 'your-secret-key'
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(days=3)

executor = ThreadPoolExecutor()
oauth = OAuth(app)
db.init_app(app)
jwt = JWTManager(app)
CORS(app)
api = Api(app)
app.app_context().push()

"===========================================================UTILS====================================================================================="

@jwt.user_identity_loader
def user_identity_lookup(member):
    return member.to_json()

google = oauth.remote_app(
    'google',
    consumer_key='1067657484179-q3cm9n8crjn82rkoi8fbid1uu00rihg6.apps.googleusercontent.com',
    consumer_secret='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',
    request_token_params={
        'scope': 'email',
    },
    base_url='https://www.googleapis.com/oauth2/v1/',
    authorize_url='https://accounts.google.com/o/oauth2/auth',
    request_token_url=None,
    access_token_method='POST',
    access_token_url='https://oauth2.googleapis.com/token',
)


"===========================================================RESOURCE====================================================================================="


class ProcessAudioResource(Resource):
    @jwt_required()
    def post(self):
        try:

            # Save the received audio file in the current directory
            audio_file = request.files['audio']
            audio_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'input_audio.wav')
            audio_file.save(audio_path)

            # Asynchronously transcribe
            future = executor.submit(self.transcribe_audio, audio_path)

            # Continue processing other tasks while waiting for transcription result
            other_task_result = self.do_other_task()

            # Wait for transcription result
            transcription_result = future.result()

            text_to_translate = transcription_result["text"]
            source_language = "..."

            # translated_text = translate_to_english(text_to_translate, source_language)
            new_transcription = Transcription(text=text_to_translate, language=source_language, user_id=get_jwt_identity()['id'])

            db.session.add(new_transcription)
            db.session.commit()

            # Get transcriptions for the current user
            user_transcriptions = Transcription.query.filter_by(user_id=get_jwt_identity()['id']).all()

            # Extract words from transcriptions
            all_words = [word.lower() for transcription in user_transcriptions for word in transcription.text.split()]

            # Count the frequency of each word
            word_counts = Counter(all_words)
            save_word_frequencies(get_jwt_identity()['id'], word_counts)
            all_phrases = [phrase.lower() for transcription in user_transcriptions for phrase in transcription.text.split('.') if phrase != ""]
            
            phrase_counts = Counter(all_phrases)

            # Identify the top 3 unique phrases
            top_unique_phrases = get_top_unique_phrases(phrase_counts)

            # Save top unique phrases to the UniquePhrase table
            save_top_unique_phrases(get_jwt_identity()['id'], top_unique_phrases)

            # Clean up the audio file
            os.remove(audio_path)

            return {
                "language": source_language,
                "translated_text": text_to_translate
            }

        except FileNotFoundError as e:
            return {"error": f"File not found: {str(e)}"}, 404
        except Exception as e:
            return {"error": f"An unexpected error occurred: {str(e)}"}, 500

    def transcribe_audio(self, audio_path):
        audio_file= open(audio_path, "rb")

        transcript = openai.Audio.translate(model="whisper-1", file = audio_file)
        print("i","transcript",transcript)
        return transcript

    def do_other_task(self):
        # Simulate another task
        return "Other task result"

api.add_resource(ProcessAudioResource, '/processAudio')




"===========================================================USER MANAGEMENT====================================================================================="
@app.route('/google_login')
def google_login():
    return google.authorize(callback=url_for('authorized', _external=True))


@app.route('/google_logout')
def logout():
    session.pop('google_token', None)
    return redirect(url_for('index'))


@app.route('/login/authorized')
def authorized():
    response = google.authorized_response()
    if response is None or response.get('access_token') is None:
        return 'Access denied: reason={} error={}'.format(
            request.args['error_reason'],
            request.args['error_description']
        )
    
    session['google_token'] = (response['access_token'], '')
    user_info = google.get('userinfo')
  
    email = user_info.data['email']
    username = re.match(r'([^@]+)@', email).group(1)

    password = user_info.data['id']
    user = User.query.filter_by(email=email, password=password).first()

    if user:
        access_token = create_access_token(identity=user)
        return redirect(f'https://resonance-sigma.vercel.app/auth/{access_token}')
    else:
        new_user = User(username=username,email=email,password=password)
        db.session.add(new_user)
        db.session.commit()
        access_token = create_access_token(identity=new_user)

        return redirect(f'https://resonance-sigma.vercel.app/auth/{access_token}')
        




@google.tokengetter
def get_google_oauth_token():
    return session.get('google_token')


@app.route('/register', methods=['POST'])
def register():
    data = request.get_json()

    # Check if the username already exists
    existing_user = User.query.filter_by(username=data['username']).first()
    if existing_user:
        return jsonify({'error': 'Username already exists'})

    # Create a new user
    new_user = User(username=data['username'], email=data['email'], password=data['password'])

    # Add the new user to the database
    db.session.add(new_user)
    db.session.commit()

    return jsonify({'message': 'Registration successful'}), 201

# Endpoint for user login
@app.route('/login', methods=['POST'])
def login():
    data = request.json
    email = data.get('email')
    password = data.get('password')

    user = User.query.filter_by(email=email, password=password).first()

    if user:
        access_token = create_access_token(identity=user)
        return jsonify(access_token=access_token,user=user.to_json()), 200
    else:
        return jsonify({"error": "Invalid credentials"}), 401

@app.route('/user', methods=['GET'])
@jwt_required()
def protected():
    try:
        current_user = get_jwt_identity()
        print(current_user)
        return jsonify(user=current_user), 200
    except Exception as e:
        return jsonify({"error": f"An unexpected error occurred: {str(e)}"}), 500


"===========================================================WORD FREQUENCIES====================================================================================="

@app.route('/word_frequencies')
@jwt_required()
def word_frequencies():
    # Replace 'current_user_id' with the actual way you get the current user's ID
    current_user_id = get_jwt_identity()['id']

    # Get transcriptions for the current user
    user_transcriptions = Transcription.query.filter_by(user_id=current_user_id).all()

    # Extract words from transcriptions
    all_words = [word.lower() for transcription in user_transcriptions for word in transcription.text.split()]

    # Count the frequency of each word
    word_counts = Counter(all_words)

    # Limit the number of most common words to 10
    most_common_words = word_counts.most_common(10)[:10]
    user_common_words = {common_words[0]: common_words[1] for common_words in most_common_words}
    words = [word for word in user_common_words]

    # Get word frequencies for all users
    all_users_word_counts = other_user_counts(user_common_words,current_user_id)
    
    return jsonify({
        "words":words,
        "current_user_word_counts": user_common_words,
        "all_users_word_counts": all_users_word_counts
    })


"===========================================================WORD FREQUENCIES HELPER FUNCTIONS====================================================================================="

def save_word_frequencies(user_id, word_counts):
    # Save word frequencies to the WordFrequency table for the current user
   
    for word, count in word_counts.items():
        word_frequency = WordFrequency.query.filter_by(user_id=user_id, word=word).first()
        if word_frequency:
            word_frequency.count += count
        else:
            new_word_frequency = WordFrequency(user_id=user_id, word=word, count=count)
            db.session.add(new_word_frequency)
    db.session.commit()

def other_user_counts(current_user_count, user_id):
    words_by_others = defaultdict(int)
    
    for word in current_user_count:
        word_objects = WordFrequency.query.filter(
            (WordFrequency.word == word) & (WordFrequency.user_id != user_id)
        ).all()
        print({word.word:word.count for word in word_objects})
        for word_object in word_objects:
            words_by_others[word_object.word] += word_object.count
    
    return dict(words_by_others)



"===========================================================TOP UNIQUE PHRASES====================================================================================="

@app.route('/top_unique_phrases')
@jwt_required()
def top_unique_phrases():
    # Replace 'current_user_id' with the actual way you get the current user's ID
    current_user_id = get_jwt_identity()['id']

    # Get transcriptions for the current user
    user_transcriptions = Transcription.query.filter_by(user_id=current_user_id).all()

    # Extract phrases from transcriptions
    
    all_phrases = [phrase.lower() for transcription in user_transcriptions for phrase in transcription.text.split('.') if phrase != ""]


    # Count the frequency of each phrase
    phrase_counts = Counter(all_phrases)

    # Identify the top 3 unique phrases
    top_unique_phrases = get_top_unique_phrases(phrase_counts)


    return jsonify({'top_unique_phrases': top_unique_phrases})

"===========================================================TOP UNIQUE PHRASES HELPER FUNCTIONS====================================================================================="

def get_top_unique_phrases(phrase_counts):
    # Identify the top 3 unique phrases
    top_unique_phrases = [phrase for phrase, count in phrase_counts.most_common(3)]
    return top_unique_phrases

def save_top_unique_phrases(user_id, top_unique_phrases):
    # Save top unique phrases to the UniquePhrase table for the current user
    for phrase in top_unique_phrases:
        unique_phrase = UniquePhrase.query.filter_by(user_id=user_id, phrase=phrase).first()
        if not unique_phrase:
            new_unique_phrase = UniquePhrase(user_id=user_id, phrase=phrase)
            db.session.add(new_unique_phrase)
    db.session.commit()

"===========================================================COSINE SIMILARITY====================================================================================="

def calculate_cosine_similarity(user_text, other_texts):
    vectorizer = TfidfVectorizer()
    vectors = vectorizer.fit_transform([user_text] + other_texts)
    similarity_matrix = cosine_similarity(vectors)
    user_similarity = similarity_matrix[0][1:]
    return user_similarity



@app.route('/similar_users', methods=['POST'])
@jwt_required()
def find_similar_users():
    data = request.get_json()

    current_user_id = get_jwt_identity()['id']
    current_user_transcription = data.get('text')

    # Get transcriptions of other users
    other_users_transcriptions = Transcription.query.filter(Transcription.user_id != current_user_id).all()
    other_texts = [transcription.text for transcription in other_users_transcriptions]

    # Calculate cosine similarity
    # Find most similar users above a certain threshold
    similarities = calculate_cosine_similarity(current_user_transcription, other_texts)

    threshold = 0.3  # Set your desired threshold here
    similar_user_indices = [index for index, similarity in enumerate(similarities) if similarity >= threshold]

    # Collect information about similar users
    similar_users = []
    for index in similar_user_indices:
        user_id = other_users_transcriptions[index].user_id
        user = User.query.get(user_id)
        similarity_score = similarities[index]
        similar_user_info = {
            'user_id': user_id,
            'similarity_score': similarity_score,
            'user_data': user.to_json(),
            'transcription': other_users_transcriptions[index].text,
            # Assuming User.to_json() returns user data as JSON
        }
        similar_users.append(similar_user_info)

    similar_users = sorted(similar_users, key=lambda x: x['similarity_score'], reverse=True)
    return jsonify(similar_users)



@app.route('/history')
@jwt_required()
def history():
    transcriptions = Transcription.query.filter(Transcription.user_id == get_jwt_identity()['id'])
    data = [{"text": transcript.text, "language": transcript.language} for transcript in transcriptions]
    return jsonify(history=data)
@app.route('/')
def backend():
    return "Backend Connected"

"===========================================================MAIN====================================================================================="

if __name__ == '__main__':
    app.run(debug=True)
