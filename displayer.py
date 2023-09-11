from PIL import Image, ImageSequence
import numpy as np
from os import listdir, makedirs
from os.path import isfile, join
import cv2
from time import time, sleep
import threading
from queue import Queue
from file_processor import get_file_extension
import os.path
import board
import neopixel
from rpi_ws281x import Adafruit_NeoPixel

class Displayer:
    def __init__(self, file_list=[], duration_of_files_seconds=10, on=True, brightness=0.5):
        '''
            parameters:
                file_list (str[]): A list of paths to files of type '.png', '.gif', or '.mp4' to display in rotation. Defaults to []

                duration_of_files_seconds (float): The number of seconds each file in file_list should be displayed before displaying. Defaults to 10

                on (bool): Whether the files in file_list should be displayed in rotation. Defaults to True

                brightness (float): The brightness the screen should be set to in the range [0, 100]. Defaults to 50
        '''
        self.file_list = file_list
        self.file_list_lock = threading.Lock()

        self.duration_ms = duration_of_files_seconds * 1000
        self.duration_lock = threading.Lock()

        self.on = on
        self.on_lock = threading.Lock()

        # self.lights = neopixel.NeoPixel(board.D18, 1024, auto_write=False, brightness=brightness)
        self.lights = Adafruit_NeoPixel(1024, 18, 800_000, 10, False, 255)
        self.lights.begin()
        self.brightness = brightness
        self.lights_lock = threading.Lock()

        self.curr_file_start_time_ms = None
        self.worker_thread, self.worker_start_queue, self.worker_kill_queue = None, None, None
        self.next_thread, self.next_start_queue, self.next_kill_queue = None, None, None
        self.worker_file_path, self.worker_file_idx = None, None
        self.next_file_path, self.next_file_idx = None, None

        self.worker_lock, self.next_lock = threading.Lock(), threading.Lock()

        self.show_loading_animation = False

        # each buffer can store at most 100,000 frames
        # each frame can be at most (32*32*3 + 4) bytes: 32x32 pixels * RGB + 4 bytes for frame duration in case of gifs
        # allowing 500mb / ((32*32*3 + 4) bytes) * 2 gives us ~80,000 frames.
        # multiplied by 2 for margin of safety
        self.BUFFER_SIZE = 100_000


        '''
        LOCKS SHOULD ALWAYS BE AQUIRED AND RELEASED IN THE SAME ORDER TO AVOID DEADLOCKS:
            self.file_list_lock
            self.worker_lock
            self.next_lock
            self.duration_lock
            self.on_lock
            self.lights_lock
        '''
        
    def turn_on(self):
        '''Enables displaying of files in file_list'''
        self.on_lock.acquire()
        self.on = True
        self.on_lock.release()

    def turn_off(self):
        '''Disables displaying of files in file_list'''
        self.worker_lock.acquire()
        self.next_lock.acquire()
        self.on_lock.acquire()

        self.on = False

        self._kill_worker_thread()
        self._kill_next_worker_thread()

        self._reset_lights()

        self.on_lock.release()
        self.next_lock.release()
        self.worker_lock.release()
        
    def update_file_durations(self, new_duration_seconds):
        '''Sets the duration for which each file in file_list will be displayed.
        
            Parameters: 
                new_duration_seconds: the duration each file should be displayed for in seconds
        '''
        self.duration_lock.acquire()
        self.duration_ms = float(new_duration_seconds) * 1000
        self.duration_lock.release()

    def get_file_durations(self):
        '''Returns the duration each file is displayed for in seconds'''
        self.duration_lock.acquire()
        duration = self.duration_ms / 1000
        self.duration_lock.release()
        return duration
    
    def update_file_list(self, new_file_list):
        self.file_list_lock.acquire()
        self.worker_lock.acquire()
        self.next_lock.acquire()
        self.on_lock.acquire()

        self.file_list = [file for file in new_file_list if os.path.exists(file)]
        not_found_files = [file for file in new_file_list if file not in self.file_list]

        self._kill_worker_thread()

        self._kill_next_worker_thread()

        if self.on:
            self._initialize_worker_and_next_threads()

        self.on_lock.release()
        self.next_lock.release()
        self.worker_lock.release()
        self.file_list_lock.release()    

        return not_found_files

    def get_files_in_rotation(self):
        '''Returns a list of file paths as strings to the files that are currently in file_lists and will be displayed in rotation'''
        self.file_list_lock.acquire()
        copy = self.file_list.copy()
        self.file_list_lock.release()
        return copy
    
    def update_brightness(self, brightness):
        '''Updates the screen brightness
        
        Parameters:
            brightness (float): The brightness value in the range [0, 1] to set the screen to

        Will clip brightness to 0 if value is less than 0, and to 1 if value is greater than 1. 
        '''
        self.brightness = min(1, max(0, brightness))

        self.lights_lock.acquire()
        self.lights.setBrightness(int(255*self.brightness))
        self.lights_lock.release()

    def get_brightness(self):
        '''Returns the current brightness value the screen is set to in the range [0, 100]
        
        returns: (int)
        '''
        return int(self.brightness * 100)
    
    def _display_png(self, png_path, start_queue, kill_queue):
        '''Displays the .png file at the provided path on the screen.
        
        Parameters:
            png_path (str): The path to the .png file

        Will raise exception if the .png file cannot be found or opened
        '''

        # lights only require being sent frame once for png since there is no next frame
        # this function is threaded so that code to start displaying a file is agnostic to function type
        # this allows more streamlined code at very little cost
        # 200 ms is used for sleep value to allow other threads to run for a considerable amount of time while also
        # having rapid respons to start and kill messages 
        while start_queue.empty() and kill_queue.empty():
            sleep(.2)

        while kill_queue.empty():
            with Image.open(png_path).convert('RGB') as png:
                self._display_frame(np.array(png))
            sleep(.2)
        
        self._reset_lights()
        
    def _load_gif_buffer(self, gif_path: str, frames_buffer: Queue(), kill_queue: Queue()):
        with Image.open(gif_path) as gif:
            n_frames = gif.n_frames
            frame_idx = 0
            while kill_queue.empty():
                if frames_buffer.qsize() < self.BUFFER_SIZE:
                    gif.seek(frame_idx)
                    frame_idx = (frame_idx + 1) % n_frames 
                    frames_buffer.put((np.asarray(gif.convert('RGB')), gif.info.get('duration')))
                    
    def _display_gif(self, gif_path, start_queue, kill_queue):
        frames_buffer, buffer_kill_queue = Queue(), Queue()
        buffer_filler = threading.Thread(target=self._load_gif_buffer, args=(gif_path, frames_buffer, buffer_kill_queue))
        buffer_filler.start()
        
        # not using sleep allows much more rapid response to start and kill sentinels in their respective queues
        # but wastes cpu cycles 
        # sleep suspends this thread which saves cpu cycles and also allows buffer_filler thread to have as much time as possible to fill the buffer
        # here 200 milliseconds is used for a nice balance between giving buffer_filler time while also not taking too long to respond to start_queue sentinel
        # after starting we sleep for the amount of time the frame should stay up
        while start_queue.empty() and kill_queue.empty():
            sleep(.2)
        
        def skip_to_keep_up():
            display_time = time() * 1000
            frame = None
            frame_number = -1
            prev_frame_number = -1

            frame_to_display = lambda: int((time() * 1000 - display_start_time)/frame_time_ms)
                
            while kill_queue.empty():
                # recalculates frame to display on every iteration because buffer.get() can hang if buffer is 
                while display_time < time() * 1000:
                    frame, duration_ms = frames_buffer.get()
                    display_time += duration_ms
                    frame_number += 1
                
                if prev_frame_number != frame_number:
                    self._display_frame(frame)
                prev_frame_number = frame_number

        display_running_time = time() * 1000
        frame_duration_ms = None
        frame_number = 0
        frame = None
        while kill_queue.empty():
            while display_running_time < time() * 1000:
                frame, frame_duration_ms = frames_buffer.get()
                display_running_time += frame_duration_ms
                frame_number += 1
            self._display_frame(frame)
            # strategy 1: 
            # frame_start_time_ms = time() * 1000
            # frame, frame_duration_ms = frames_buffer.get()
            # self._display_frame(frame)
            # time_it_took_to_flash_frame_ms = time() * 1000 - frame_start_time_ms
            # sleep(max(0, (frame_duration_ms - time_it_took_to_flash_frame_ms)/1000))

        self._reset_lights()
        buffer_kill_queue.put(object())    
        buffer_filler.join()

    def _load_mp4_buffer(self, mp4_path: str, frames_buffer: Queue(), kill_queue: Queue()):
        mp4_capture = cv2.VideoCapture(mp4_path)
        n_frames = int(mp4_capture.get(cv2.CAP_PROP_FRAME_COUNT))
        frame_idx = 0
        while kill_queue.empty():
            if frames_buffer.qsize() < self.BUFFER_SIZE:
                success, frame = mp4_capture.read()
                if success:
                    frame = frame[...,::-1]
                    frames_buffer.put(frame)
                
                if not success and not frame:
                    mp4_capture = cv2.VideoCapture(mp4_path)

                frame_idx = (frame_idx + 1) % n_frames


    def _display_mp4(self, mp4_path, start_queue, kill_queue):
        vidcap = cv2.VideoCapture(mp4_path)
        fps = round(vidcap.get(cv2.CAP_PROP_FPS), 5)
        frame_time_ms = round(1/fps, 5) * 1000
        frames_buffer, buffer_kill_queue = Queue(), Queue()
        buffer_filler = threading.Thread(target=self._load_mp4_buffer, args=(mp4_path, frames_buffer, buffer_kill_queue))
        buffer_filler.start()
        
        # not using sleep allows much more rapid response to start and kill sentinels in their respective queues
        # but wastes cpu cycles 
        # sleep suspends this thread which saves cpu cycles and also allows buffer_filler thread to have as much time as possible to fill the buffer
        # here 200 milliseconds is used for a nice balance between giving buffer_filler time while also not taking too long to respond to start_queue sentinel
        # after starting we sleep for the amount of time the frame should stay up
        while start_queue.empty() and kill_queue.empty():
            sleep(.2)
                
        def skip_to_keep_up():
            display_start_time = time() * 1000
            frame = None
            frame_number = -1
            prev_frame_number = -1

            frame_to_display = lambda: int((time() * 1000 - display_start_time)/frame_time_ms)
                
            while kill_queue.empty():
                # recalculates frame to display on every iteration because buffer.get() can hang if buffer is 
                while frame_number < frame_to_display():
                    frame = frames_buffer.get()
                    frame_number += 1
                
                if prev_frame_number != frame_number:
                    self._display_frame(frame)
                prev_frame_number = frame_number

        def constant_frame_rate():
            fps = 10 # CAN TRY DIFFERENT VALUES 10 SEEMS TO WORK BEST BECAUSE NEOPIXELS ARE TOO SLOW.
            frame_time_ms = (1/fps) * 1000

            while kill_queue.empty():
                display_time = time() * 1000
                self._display_frame(frames_buffer.get())
                ms_it_took_to_display = time() * 1000 - display_time
                sleep(max(0, (frame_time_ms - ms_it_took_to_display)/1000))

        while kill_queue.empty():
            skip_to_keep_up()
            # constant_frame_rate()
        
        self._reset_lights()
        buffer_kill_queue.put(object())
        buffer_filler.join()
        
    def display_loading_animation(self):
        pass

    def _display_frame(self, frame):
        # coordinate transform required because the LED wall is technically one strip of 1024 LEDS
        # the way the LED's were layed means that the top left corner (which is coords (0, 0) in frame array)
        # is LED 1023. Even rows and odd rows go in opposite directions.
        # Example 3x3 LED Wall 
        #   (0,0), 8        (0, 1), 7        (0, 2), 6 
        #   (1,0), 5        (1, 1), 4        (1, 2), 3
        #   (2,0), 2        (2, 1), 1        (2, 2), 0
        #   
        def transform_coords(row, col):
            if row % 2 == 0:
                return 1023 - ((row * 32) + col)
        
            return 1023 - ((((row + 1) * 32) - 1) - col)

        self.lights_lock.acquire()
        frame = np.array(frame)

        for i in range(frame.shape[0]):
            for j in range(frame.shape[1]):
                # self.lights[transform_coords(i, j)] = frame[i, j]
                r,g,b = frame[i,j]
                self.lights.setPixelColorRGB(transform_coords(i, j), r, g, b)
        self.lights.show()
        self.lights_lock.release()

    def _reset_lights(self):
        frame = np.zeros((32, 32, 3)).astype(np.uint8)
        self._display_frame(frame)

    def _get_worker_func_from_path(self, path):
        file_extension = get_file_extension(path)  
        if file_extension == '.png':
            worker_func = self._display_png
        elif file_extension == '.gif':
            worker_func = self._display_gif
        elif file_extension == '.mp4':
            worker_func = self._display_mp4
        else:
            raise Exception(f'Unexpected file type: file "{path}" has unexpected extension "{file_extension}". Only files of type ".png", ".gif", or ".mp4" are accepted.')
        
        return worker_func
    
    def _create_display_thread_and_queues(self, file_path):
        start_queue, kill_queue = Queue(), Queue()
        func = self._get_worker_func_from_path(file_path)
        thread = threading.Thread(target=func, args=(file_path, start_queue, kill_queue))
        return thread, start_queue, kill_queue
    
    def _kill_worker_thread(self):
        '''ACQUIRE self.worker_lock BEFORE CALLING THIS FUNCTION AND RELEASE AFTER'''
        if self.worker_thread and self.worker_thread.is_alive():
            self.worker_kill_queue.put(object())
            self.worker_thread.join()
        
        self.worker_thread, self.worker_start_queue, self.worker_kill_queue = None, None, None

    def _kill_next_worker_thread(self):
        '''ACQUIRE self.next_lock BEFORE CALLING THIS FUNCTION AND RELEASE AFTER'''
        if self.next_thread and self.next_thread.is_alive():
            self.next_kill_queue.put(object())
            self.next_thread.join()
        self.next_thread, self.next_start_queue, self.next_kill_queue = None, None, None

    def _initialize_worker_and_next_threads(self, worker_idx=None):
        '''MUST ACQUIRE self.file_list_lock, self.worker_lock, self.next_lock BEFORE CALLING THIS FUNCTION and release after'''
        if self.file_list:
            self.worker_file_idx = worker_idx or 0
            self.next_file_idx = (self.worker_file_idx + 1) % len(self.file_list)
            self.worker_file_path = self.file_list[self.worker_file_idx]
            self.next_file_path = self.file_list[self.next_file_idx]

            self.worker_thread, self.worker_start_queue, self.worker_kill_queue = self._create_display_thread_and_queues(self.worker_file_path)
            self.next_thread, self.next_start_queue, self.next_kill_queue = self._create_display_thread_and_queues(self.next_file_path)
            
            self.worker_thread.start()
            self.worker_start_queue.put(object())
            self.curr_file_start_time_ms = time() * 1000
            
            self.next_thread.start()

    def run(self):
        '''
        Calling thread.start() on a thread does not make that new thread display its file but instead causes it to load the file in a buffer. Once anything is put in the start_queue for that thread,
        the thread will begin to display the file by reading from the preloaded buffer.
        '''
        self._initialize_worker_and_next_threads()

        while True:
            sleep(.05)

            self.worker_lock.acquire()
            self.next_lock.acquire()
            self.on_lock.acquire()
            if not self.on:
                self._kill_worker_thread()
                self._kill_next_worker_thread()
                self.on_lock.release()
                self.next_lock.release()    
                self.worker_lock.release()
                continue
            else:
                self.on_lock.release()
                self.next_lock.release()    
                self.worker_lock.release()


            self.file_list_lock.acquire()
            if not self.file_list:
                self.file_list_lock.release()
                continue
            else:
                self.file_list_lock.release()
            
            self.file_list_lock.acquire()
            self.worker_lock.acquire()
            self.next_lock.acquire()
            self.on_lock.acquire()
            if self.on and (not self.worker_thread or not self.worker_thread.is_alive()) and (not self.next_thread or not self.next_thread.is_alive()):
                self._initialize_worker_and_next_threads()
            self.on_lock.release()
            self.next_lock.release()
            self.worker_lock.release()
            self.file_list_lock.release()

            self.duration_lock.acquire()
            file_duration_ms = self.duration_ms
            self.duration_lock.release()

            curr_time_ms = time() * 1000
            self.file_list_lock.acquire()
            self.worker_lock.acquire()
            self.next_lock.acquire()
            self.on_lock.acquire()

            if self.on and (not self.curr_file_start_time_ms or curr_time_ms - self.curr_file_start_time_ms > file_duration_ms):
                self._kill_worker_thread()
                self.worker_thread, self.worker_start_queue, self.worker_kill_queue = self.next_thread, self.next_start_queue, self.next_kill_queue
                self.worker_file_path, self.worker_file_idx = self.next_file_path, self.next_file_idx

                self.next_file_idx = (self.worker_file_idx + 1) % len(self.file_list)
                self.next_file_path = self.file_list[self.next_file_idx]
                self.next_thread, self.next_start_queue, self.next_kill_queue = self._create_display_thread_and_queues(self.next_file_path)
                self.next_thread.start()

                self.worker_start_queue.put(object())
                self.curr_file_start_time_ms = time() * 1000
            
            self.on_lock.release()
            self.next_lock.release()
            self.worker_lock.release()
            self.file_list_lock.release()
            

# x = Displayer(['./static/uploads/mp41.mp4', './static/uploads/mp41.mp4'], 5, True, .5)
# x.run()