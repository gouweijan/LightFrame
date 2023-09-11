from PIL import Image, ImageEnhance
import numpy as np
import subprocess
from pathlib import Path
import os

def process_file(file_path, contrast):
    extension = get_file_extension(file_path) 
    if extension in ['.jpg', '.png']:
        process_image(file_path, contrast)
    elif extension == '.gif':
        process_gif(file_path, contrast)
    elif extension == '.mp4':
        process_mp4(file_path, contrast)
    else:
        raise Exception('Unsupported File Type')

def get_file_name(file_path):
    return Path(file_path).name[:-4]

def get_file_extension(file_path):
    return Path(file_path).suffix

def process_image(img_path, contrast_enhancement):
    if get_file_extension(img_path) == '.jpg':
        file_name_without_ext = os.path.splitext(img_path)[0]
        file_name_as_png = file_name_without_ext + '.png'
        os.rename(img_path, file_name_as_png)
        img_path = file_name_as_png

    new_file_path = os.path.splitext(img_path)[0] + '_processed.png'

    with Image.open(img_path, 'r') as img:
        # img = ImageEnhance.Contrast(img)
        # img = img.enhance(contrast_enhancement)
        # img = img.convert('P', palette=Image.ADAPTIVE, colors=10)
        img = img.convert('RGBA').resize((32, 32))
        newImg = Image.new('RGBA',(32,32), 'BLACK')
        newImg.paste(img, mask = img)
        newImg.save(new_file_path)

        os.remove(img_path)
        os.rename(new_file_path, img_path)
        
        return new_file_path
    
def process_gif(gif_path, contrast_enhancement):
    new_file_path = os.path.splitext(gif_path)[0] + '_proccessed.gif'
    with Image.open(gif_path) as gif:
        resized_gif = [None]*gif.n_frames
        durations = [0]*gif.n_frames
        for i in range(gif.n_frames):   
            gif.seek(i)
            resized_gif[i] = gif.resize((32, 32))
            durations[i] = gif.info['duration']
    
        resized_gif[0].save(fp=new_file_path, save_all=True, append_images=resized_gif[1:], duration=durations, loop=0)
    
    os.remove(gif_path)
    os.rename(new_file_path, gif_path)
    return new_file_path

def process_mp4(mp4_path, contrast_enhancement):
    try:
        new_file_path = os.path.splitext(mp4_path)[0] + '_processed.mp4'
        return_code = subprocess.check_call(f'ffmpeg -i {mp4_path} -vf scale=32:32 {new_file_path} -y'.split(' '))
        os.remove(mp4_path)
        os.rename(new_file_path, mp4_path)
        return new_file_path
    except Exception as e:
        raise e
    
    
def get_gif_length(gif_path):
    with Image.open(gif_path) as gif:
        n_frames = gif.n_frames
        sum = 0
        for i in range(n_frames):
            gif.seek(i)
            sum += gif['duration']

        return int(round(sum / 1000, 0))

def get_mp4_length(mp4_path):
    process = subprocess.Popen(f'ffprobe -i {mp4_path} -show_entries format=duration -v quiet -of csv="p=0"', stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    process.wait()
    out, err = process.communicate()

    return round(float(out.decode()), 0)
