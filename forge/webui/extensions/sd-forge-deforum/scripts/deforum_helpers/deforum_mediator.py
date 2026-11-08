import asyncio
import websockets
import pickle
import time
import win32pipe, win32file, pywintypes
import ast
from . import deforumation_switch

needToUpdateMediator = False
anim_args_copy = None
args_copy = None
root_copy = None
exception_in_a_row = 0
wasInterrupted = False

# DeforumationQT ON/OFF switch (above Deforum's preview). A render that starts while it is OFF, or goes on after it
# was switched OFF, sends nothing to the named pipe: every read is answered from local_values, where all overrides
# are off and every value is what Deforum itself set. The render follows Deforum's own settings and never waits for
# a mediator that is not running.
pipe_for_this_render = True
local_values = {}
last_values = {}  # the latest value Deforum sent for each parameter in this render, shown in the console table
LOCAL_ZERO_PARAMS = ("is_paused_rendering", "should_resume", "render_loopback", "seed_changed",
                     "is_inside_total_recall_range", "deforum_interrupted", "start_frame")


def mediator_begin_render(anim_args, args, root):
    global pipe_for_this_render
    mediator_set_anim_args(anim_args, args, root)
    local_values.clear()
    last_values.clear()
    unsupported_params.clear()  # a DeforumationQT that was updated meanwhile gets asked again
    pipe_for_this_render = deforumation_switch.is_enabled()
    return pipe_for_this_render


def mediator_using_pipe():
    return pipe_for_this_render and deforumation_switch.is_enabled()


def mediator_last_value(param, default):
    return last_values.get(param, default)


def local_getValue(param):
    if param in local_values:
        return str(local_values[param])
    if param == "should_use_optical_flow":  # keep Deforum's own optical flow setting
        return "0" if str(getattr(anim_args_copy, "optical_flow_cadence", "None")) == "None" else "1"
    if param in ("cadence_flow_factor", "generation_flow_factor"):
        return "1"
    if param.startswith(("should_use", "use_", "cn_udcn")) or param in LOCAL_ZERO_PARAMS:
        return "0"
    own_values = {
        "seed": getattr(args_copy, "seed", 0),
        "steps": getattr(args_copy, "steps", 0),
        "seed_behavior": getattr(args_copy, "seed_behavior", "iter"),
        "seed_iter_n": getattr(args_copy, "seed_iter_N", 1),
        "cadence": getattr(anim_args_copy, "diffusion_cadence", 1),
    }
    return str(own_values.get(param, 0))


def local_setValue(param, value):
    local_values[param] = value
    return value


# With DeforumationQT ON the render takes its steps from DeforumationQT's Sampler Steps slider, so Deforum's own Steps
# field is sent there: right away when a sampler preset sets it, and at the start of a render when it changed since the
# last send. Whichever changed last, Deforum's field or DeforumationQT's slider, is what the render uses.
forge_steps_sent = None


def mediator_try_setValues(values):
    # Only while the switch is ON and the mediator is running. The mediator serves one connection at a time and a render
    # talks to it many times per frame, so each value gets about a second to find the pipe free, and then it gives up.
    global forge_steps_sent
    if not deforumation_switch.is_enabled() or not deforumation_switch.mediator_is_running():
        return False
    for param, value in values.items():
        for attempt in range(40):
            try:
                asyncio.run(sendAsync([1, param, value]))
                break
            except Exception as e:
                last_error = e
                time.sleep(0.025)
        else:
            print(f"DeforumationQT did not get {param} = {value}: {last_error}")
            return False
        if param == "steps":
            forge_steps_sent = int(value)
    return True


def mediator_send_steps(steps):
    global forge_steps_sent
    if int(steps) != forge_steps_sent:
        mediator_setValue("steps", int(steps))
        forge_steps_sent = int(steps)


async def sendAsync(value):
    bufSize = 64 * 1024
    handle = win32file.CreateFile('\\\\.\\pipe\\Deforum', win32file.GENERIC_READ | win32file.GENERIC_WRITE, 0, None,win32file.OPEN_EXISTING, 0, None)
    res = win32pipe.SetNamedPipeHandleState(handle, win32pipe.PIPE_READMODE_MESSAGE, None, None)
    bytesToSend = pickle.dumps(value)
    win32file.WriteFile(handle, bytesToSend)
    result, data = win32file.ReadFile(handle, bufSize)
    message = data
    while len(data) == bufSize:
        print("More data has to be read (normal pipe async):"+ str(len(data)))
        result, data = win32file.ReadFile(handle, bufSize)
        message += data

    win32file.CloseHandle(handle)
    message = pickle.loads(message)
    return message[0]

def mediator_wasInterrupted():
    global wasInterrupted
    print("Was I interrupted?: -> " + str(wasInterrupted))
    if wasInterrupted:
        wasInterrupted = False
        return True
    return False

def mediator_set_anim_args(anim_args, args, root):
    global anim_args_copy
    global args_copy
    global root_copy
    anim_args_copy = anim_args
    args_copy = args
    root_copy = root

def updateMediator(): #No validation is made that  the websocket server (Mediator.py is actually up and running)
    global wasInterrupted
    wasInterrupted = True
    print("Was ordered to update time_string and was I interrupted?:->" + str(wasInterrupted))
    if anim_args_copy.resume_from_timestring:
        print("Sending Values:" + str(anim_args_copy.resume_timestring))
        return_value = asyncio.run(sendAsync([1, "resume_timestring", anim_args_copy.resume_timestring]))
        #mediator_setValue("resume_timestring", anim_args_copy.resume_timestring)
    else:
        print("Sending Values:" + str(root_copy.timestring))
        return_value = asyncio.run(sendAsync([1, "resume_timestring", root_copy.timestring]))
        #mediator_setValue("resume_timestring", root_copy.timestring)
    #OUTDIR is the same for either you resume or not.
    mediator_setValue("frame_outdir", args_copy.outdir, True)


def mediator_getValue(param, keepInterruptStatus = False):
    global needToUpdateMediator
    global exception_in_a_row
    global wasInterrupted
    checkerrorConnecting = True
    needToUpdateMediator = False
    exception_in_a_row = 0
    if param != "is_paused_rendering" and keepInterruptStatus == False:
        wasInterrupted = False
    if not mediator_using_pipe():
        return local_getValue(param)
    while checkerrorConnecting:
        try:
            return_value = asyncio.run(sendAsync([0, param, 0]))
            if needToUpdateMediator:
                needToUpdateMediator = False
                updateMediator()
            return return_value
        except Exception as e:
            exception_in_a_row += 1
            if exception_in_a_row > 20:
                print("Deforum Mediator (Piped version) Error:" + str(e))
                print("...while trying to send parameter ("+str(param)+")")
                print("The Deforumation Mediator, is probably not connected (waiting 1.0 seconds, before trying to reconnect...)")
                needToUpdateMediator = True
                exception_in_a_row = 0
            if not deforumation_switch.is_enabled():
                print("DeforumationQT was switched OFF: this render goes on with Deforum's own settings.")
                return local_getValue(param)
            time.sleep(0.05)

# Parameters only a patched DeforumationQT mediator knows (see live_control.py). An older one answers
# "NO SUCH COMMAND", and then the value is left to Deforum and is not asked for again in this render.
unsupported_params = set()


def mediator_get_optional(param, default=""):
    """Read a parameter DeforumationQT may not have. It never waits: a busy or missing pipe gives the default."""
    if not mediator_using_pipe() or param in unsupported_params:
        return default
    for attempt in range(3):
        try:
            value = str(asyncio.run(sendAsync([0, param, 0]))).strip()
        except Exception:
            time.sleep(0.02)
            continue
        if value.startswith("NO SUCH COMMAND"):
            unsupported_params.add(param)
            print("Deforum: this DeforumationQT does not know '" + param + "', so Deforum keeps that one."
                  " Update DeforumationQT's mediator.py to control it from there.")
            return default
        return value
    return default


def mediator_setValue(param, value, keepInterruptStatus = False):
    global needToUpdateMediator
    global exception_in_a_row
    global wasInterrupted
    checkerrorConnecting = True
    needToUpdateMediator = False
    exception_in_a_row = 0
    if param != "is_paused_rendering" and keepInterruptStatus == False:
        wasInterrupted = False
    last_values[param] = value
    if not mediator_using_pipe():
        return local_setValue(param, value)
    while checkerrorConnecting:
        try:
            return_value = asyncio.run(sendAsync([1, param, value]))
            if needToUpdateMediator:
                needToUpdateMediator = False
                updateMediator()
            return return_value
        except Exception as e:
            exception_in_a_row += 1
            if exception_in_a_row > 20:
                print("Deforum Mediator Error:" + str(e))
                print("...while trying to send parameter ("+str(param)+") with value("+str(value)+")")
                print("The Deforumation Mediator, is probably not connected (waiting 1.0 seconds, before trying to reconnect...)")
                needToUpdateMediator = True
                exception_in_a_row = 0
            if not deforumation_switch.is_enabled():
                print("DeforumationQT was switched OFF: this render goes on with Deforum's own settings.")
                return local_setValue(param, value)
            time.sleep(0.05)
