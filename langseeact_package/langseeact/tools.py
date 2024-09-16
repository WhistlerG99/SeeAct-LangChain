import logging
import os
import string
from datetime import datetime
from os.path import dirname
from playwright.async_api import async_playwright,Locator

from .browser_helper import normal_launch_async, normal_new_context_async, \
    get_interactive_elements_with_playwright, select_option, saveconfig

from .format_prompt_utils import get_index_from_option_name, generate_new_query_prompt, \
    generate_new_referring_prompt, format_options, generate_option_name

from .format_prompt import format_choices, postprocess_action_lmm


def generate_option_name(index):
    if index < 26:
        return string.ascii_uppercase[index]
    else:
        first_letter_index = (index - 26) // 26
        second_letter_index = (index - 26) % 26
        first_letter = string.ascii_uppercase[first_letter_index]
        second_letter = string.ascii_uppercase[second_letter_index]
        return f"{first_letter}{second_letter}"
    

class Browser:
    def __init__(self,
                 save_file_dir="seeact_agent_files",
                 default_website="https://www.google.com/",
                 crawler_mode=False,
                 headless=False,
                 args=[],
                 browser_app="chrome",
                 persistant=False,
                 persistant_user_path="",
                 save_video=False,
                 viewport={
                     "width": 1280,
                     "height": 720
                 },
                 tracing=False,
                 trace={
                     "screenshots": True,
                     "snapshots": True,
                     "sources": True
                 },
                 logger=None,
                 ):

        self.config = {
            "basic": {
                "save_file_dir": save_file_dir,
                "default_website": default_website,
                "crawler_mode": crawler_mode,
            },
            "browser": {
                "headless": headless,
                "args": args,
                "browser_app": browser_app,
                "persistant": persistant,
                "persistant_user_path": persistant_user_path,
                "save_video": save_video,
                "viewport": viewport,
                "tracing": tracing,
                "trace": trace
                }
        }

        self.session_control = {
            'active_page': None,
            'context': None,
            'browser': None
        }

        self.main_path = os.path.join(self.config["basic"]["save_file_dir"], datetime.now().strftime("%Y%m%d_%H%M%S"))
        os.makedirs(self.main_path, exist_ok=True)

        self.action_space = ["CLICK", "PRESS ENTER", "HOVER", "SCROLL UP", "SCROLL DOWN", "NEW TAB", "CLOSE TAB",
                             "GO BACK", "GO FORWARD",
                             "TERMINATE", "SELECT", "TYPE", "GOTO", "MEMORIZE"]  # Define the list of actions here

        self.no_value_op = ["CLICK", "PRESS ENTER", "HOVER", "SCROLL UP", "SCROLL DOWN", "NEW TAB", "CLOSE TAB",
                            "PRESS HOME", "PRESS END", "PRESS PAGEUP", "PRESS PAGEDOWN"
                                                                       "GO BACK",
                            "GO FORWARD",
                            "TERMINATE", "NONE"]

        self.with_value_op = ["SELECT", "TYPE", "GOTO", "MEMORIZE", "SAY"]

        self.no_element_op = ["PRESS ENTER", "SCROLL UP", "SCROLL DOWN", "NEW TAB", "CLOSE TAB", "GO BACK", "GOTO",
                              "PRESS HOME", "PRESS END", "PRESS PAGEUP", "PRESS PAGEDOWN",
                              "GO FORWARD",
                              "TERMINATE", "NONE", "MEMORIZE", "SAY"]

        self.logger = logger
        self.time_step = -1


    async def page_on_close_handler(self):
        # Corrected to use 'self' for accessing class attributes
        if self.session_control['context']:
            try:
                await self.page.title()
            except:
                self.logger.info(
                    "The active tab was closed. Will switch to the last page (or open a new default google page)")
                if self.session_control['context'].pages:
                    self.page = self.session_control['context'].pages[-1]
                    await self.page.bring_to_front()
                    self.logger.info(f"Switched the active tab to: {self.page.url}")
                else:
                    self.page = await self.session_control['context'].new_page()
                    try:
                        await self.page.goto("https://www.google.com/", wait_until="load")
                    except Exception as e:
                        self.logger.info(f"Failed to navigate to Google: {e}")
                    self.logger.info(f"Switched the active tab to: {self.page.url}")


    async def page_on_navigation_handler(self, frame):
        # Corrected to use 'self' for accessing class attributes
        self.page = frame.page


    async def page_on_crash_handler(self, page):
        # Corrected logging method
        self.logger.info(f"Page crashed: {page.url}")
        self.logger.info("Try to reload")
        await page.reload()


    async def page_on_open_handler(self, page):
        # Added 'self' to the handler functions to reference the current instance of the class
        page.on("framenavigated", self.page_on_navigation_handler)
        page.on("close", self.page_on_close_handler)
        page.on("crash", self.page_on_crash_handler)
        self.page = page
        # Additional event listeners can be added here
        try:
            if self.config["agent"]["grounding_strategy"] == "text_choice_som": 
                with open(os.path.join(dirname(__file__), "mark_page.js")) as f:
                    mark_page_script = f.read()
                await self.session_control['active_page'].evaluate(mark_page_script)
        except Exception as e:
            pass


    async def start(self, headless=None, args=None, website=None):
        self.playwright = await async_playwright().start()
        self.session_control['browser'] = await normal_launch_async(self.playwright,
                                                                    headless=self.config['browser'][
                                                                        'headless'] if headless is None else headless,
                                                                    args=self.config['browser'][
                                                                        'args'] if args is None else args)
        self.session_control['context'] = await normal_new_context_async(self.session_control['browser'],
                                                                         viewport=self.config['browser'][
                                                                             'viewport'])

        self.session_control['context'].on("page", self.page_on_open_handler)
        await self.session_control['context'].new_page()
        
        if self.config["basic"]["crawler_mode"] is True:
            await self.session_control['context'].tracing.start(screenshots=True, snapshots=True)

        try:
            await self.page.goto(
                self.config['basic']['default_website'] if website is None else website,
                wait_until="load")
            self.logger.info(f"Loaded website: {self.config['basic']['default_website']}")
        except Exception as e:
            self.logger.info("Failed to fully load the webpage before timeout")
            self.logger.info(e)

            # await asyncio.sleep(2)


    async def get_current_page_elements(self):
        elements = await get_interactive_elements_with_playwright(self.page,
                                                                  self.config['browser']['viewport'])

        elements = sorted(elements, key=lambda el: (
            el["center_point"][1], el["center_point"][0]))  # Sorting by y and then x coordinate


        elements = [{**x, "idx": i, "option": generate_option_name(i)} for i,x in enumerate(elements)]
        return elements
    

    async def perform_action(self, target_element=None, action_name=None, value=None, element_repr=""):
        if target_element is not None:
            selector = target_element['selector']
            element_repr =target_element['description']
        else:
            selector = None

        page = self.page

        if action_name == "CLICK" and selector:
            await selector.click(timeout=2000)
            self.logger.info(f"Clicked on element: {element_repr}")
        elif action_name == "HOVER" and selector:
            await selector.hover(timeout=2000)
            self.logger.info(f"Hovered over element: {element_repr}")
        elif action_name == "TYPE" and selector:
            await selector.fill(value)
            await selector.fill(value)
            self.logger.info(f"Typed '{value}' into element: {element_repr}")
        elif action_name == "SCROLL UP":
            await page.evaluate(f"window.scrollBy(0, -{self.config['browser']['viewport']['height'] // 2});")
            self.logger.info("Scrolled up")
        elif action_name == "SCROLL DOWN":
            await page.evaluate(f"window.scrollBy(0, {self.config['browser']['viewport']['height'] // 2});")
            self.logger.info("Scrolled down")
        elif action_name == "PRESS HOME":
            await page.keyboard.press('Home')
            self.logger.info("Pressed Home key")
        elif action_name == "PRESS END":
            await page.keyboard.press('End')
            self.logger.info("Pressed End key")
        elif action_name == "PRESS PAGEUP":
            await page.keyboard.press('PageUp')
            self.logger.info("Pressed PageUp key")
        elif action_name == "PRESS PAGEDOWN":
            await page.keyboard.press('PageDown')
            self.logger.info("Pressed PageDown key")
        elif action_name == "NEW TAB":
            new_page = await self.session_control['context'].new_page()
            # self.session_control['pages'].append(new_page)
            self.logger.info("Opened a new tab")
        elif action_name == "CLOSE TAB":
            await page.close()
            self.logger.info("Closed the current tab")
        elif action_name == "GO BACK":
            await page.go_back()
            self.logger.info("Navigated back")
        elif action_name == "GO FORWARD":
            await page.go_forward()
            self.logger.info("Navigated forward")
        elif action_name == "GOTO" and value:
            await page.goto(value, wait_until="load")
            self.logger.info(f"Navigated to {value}")
        elif action_name == "PRESS ENTER" and selector:
            await selector.press('Enter')
            self.logger.info(f"Pressed Enter on element: {element_repr}")
        elif action_name == "PRESS ENTER":
            await page.keyboard.press('Enter')
            self.logger.info(f"Pressed Enter on element: {element_repr}")
        elif action_name == "SELECT" and selector:
            await select_option(selector, value)
            self.logger.info(f"Selected option '{value}' from element: {element_repr}")
        elif action_name == "TERMINATE":
            self.complete_flag = True
            self.logger.info("Task has been marked as complete. Terminating...")
        elif action_name in ["NONE"]:
            self.logger.info("No action necessary at this stage. Skipped")
        elif action_name in ["SAY"]:
            self.logger.info(f"Say {value} to the user")
        elif action_name in ["MEMORIZE"]:
            self.logger.info(f"Keep {value} to the action history.")
        else:
            raise Exception(f"Unsupported or improperly specified action: {action_name}")
        if action_name in self.no_element_op and target_element is None:
            new_action = action_name
        else:
            new_action = "[" + target_element['tag_with_role'] + "]" + " "
            new_action += target_element['description'] + " -> " + action_name
        if action_name in self.with_value_op:
            new_action += ": " + value

        # self.dev_logger.info(new_action)
        return new_action
    

    async def take_screenshot(self):
        try:
            self.time_step += 1
            await self.page.screenshot(path=self.screenshot_path)
        except Exception as e:
            self.logger.info(f"Failed to take screenshot: {e}")


    async def stop(self):

        try:
            close_context = self.session_control['context']
            self.session_control['context'] = None
            await close_context.close()
            self.logger.info("Browser context closed.")
        except Exception as e:
            self.logger.info(e)

        # final_json = {"task": self.tasks, "website": self.config["basic"]["default_website"],
        #               "num_step": len(self.taken_actions), "action_history": self.taken_actions}

        # def locator_serializer(obj):
        #     """Convert non-serializable objects to a serializable format."""
        #     if isinstance(obj, Locator):
        #         # Assuming Locator has attributes 'frame' and 'selector' you want to serialize
        #         return str(obj)
        #     raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")

        # # Using the custom default function in json.dump
        # with open(os.path.join(self.main_path, 'all_predictions.json'), 'w', encoding='utf-8') as f:
        #     json.dump(self.predictions, f, default=locator_serializer, indent=4)


        # with open(os.path.join(self.main_path, 'result.json'), 'w', encoding='utf-8') as file:
        #     json.dump(final_json, file, indent=4)
        # self.logger.info("Agent stopped.")

        # saveconfig(self.config, os.path.join(self.main_path, 'config.toml'))


    @property
    def page(self):
        if self._page is None:
            self._page = self.session_control['active_page']
        return self._page
    
    @page.setter
    def page(self, value):
        self._page = value

    @property
    def screenshot_path(self):
        return os.path.join(self.main_path, 'screenshots', f'screen_{self.time_step}.png')        