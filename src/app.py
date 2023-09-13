from flask import Flask, jsonify, render_template, request
import board 
import neopixel
import time
import random
from PIL import Image
from numpy import asarray
import numpy as np
import os
from werkzeug.utils import secure_filename
import json
from displayer import Displayer
from file_processor import process_file
import threading

# pixels = neopixel.NeoPixel(board.D18, 1024, brightness=.06, auto_write=False)
displayObject = Displayer(file_list=[], duration_of_files_seconds=10, on=False, brightness=20)
display_thread = threading.Thread(target=displayObject.run)
display_thread.start()

app = Flask(__name__,template_folder="templates", static_folder='static')

UPLOAD_FOLDER = os.path.join('static', 'uploads')
# # Define allowed files
ALLOWED_EXTENSIONS = set(['png', 'jpg', 'jpeg', 'gif', 'mp4'])
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

app.secret_key = 'This is your secret key to utilize session in Flask'

@app.route("/")
def hello():
    return render_template('site.html')
  
@app.route('/FrameLights', methods=['POST'])
def FrameLights():
    data = request.get_json() # retrieve the data sent from JavaScript
    # process the data using Python code
    result = data['value']

    if result:
        displayObject.turn_on()
    else:
        displayObject.turn_off()
        
    return jsonify(result=result) # return the result to JavaScript

@app.route('/BackLights', methods=['POST'])
def backLights():
    data = request.get_json() # retrieve the data sent from JavaScript
    # process the data using Python code
    result = data['value']

    if result:
        pixels.fill((255,255,255))
        pixels.show()
    else:
        pixels.fill((0,0,0))
        pixels.show()
    
    return jsonify(result=result) # return the result to JavaScript

@app.route('/Brightness', methods=['POST'])
def brightness():
    data = request.get_json() # retrieve the data sent from JavaScript
    # process the data using Python code
    result = data['value']

    displayObject.update_brightness(result)
    
    return jsonify(result=result) # return the result to JavaScript

@app.route('/upload',  methods=("POST", "GET"))
def uploadFile():
    if request.method == 'POST':
        # Upload file flask
        files = request.files.getlist('uploaded-file')
        # Extracting uploaded data file name
        for file in files:
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(path)
                new_file_path = process_file(path, 1)
                
        # img_file_path = session.get('uploaded_img_file_path', None)
        # Display image in Flask application web page
        return render_template('site.html', user_image = "static/uploads/elgatto.png")

@app.route('/play',  methods=("POST", "GET"))
def play():
    data = request.get_json() # retrieve the data sent from JavaScript

    if "value" in data.keys():
        value = data['value']
        # for i in range(len(value)):
        #     value[i] = value[i]#"static/uploads/"+value[i]
        displayObject.update_file_list(value)
    if "play" in data.keys():
        play = data['play']
        for i in range(len(play)):
            play[i] = "static/uploads/"+play[i]
        displayObject.update_file_list(play)
    if "num" in data.keys():
        num = data['num']
        displayObject.update_file_durations(num)
    

    # elif "play" in data.keys():
    # print(num)
    return jsonify(result="success")

@app.route('/load',  methods=("POST", "GET"))
def load():
    path = "static/uploads"
    dir_list = os.listdir(path)
    
    return jsonify(result= dir_list)

@app.route('/delete',  methods=("POST", "GET"))
def delete():
    data = request.get_json()
    result = data['value']

    for x in result: 
        print(x)
        os.remove("static/uploads/"+x)

    print(data)
    return jsonify(result= result)

  
  
if __name__ == '__main__':
    app.run(debug=True)