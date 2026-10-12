"""Send a native Xdnd file transfer to Flash Studio, without mouse/keyboard input."""
import select
import time
from Xlib import X, Xatom, display, protocol


def send_files(paths, pid, timeout=15):
    d = display.Display()
    source = None
    try:
        root = d.screen().root
        atom = d.intern_atom
        clients = root.get_full_property(atom('_NET_CLIENT_LIST'), X.AnyPropertyType)
        candidates = []
        for wid in clients.value if clients else []:
            window = d.create_resource_object('window', int(wid))
            prop = window.get_full_property(atom('_NET_WM_PID'), X.AnyPropertyType)
            if prop is not None and int(prop.value[0]) == pid:
                candidates.append(window)
        if not candidates:
            raise RuntimeError('Flash Studio window is not available on X11/XWayland yet.')
        target = max(candidates, key=lambda w: w.get_geometry().width * w.get_geometry().height)
        aware = target.get_full_property(atom('XdndAware'), X.AnyPropertyType)
        if aware is None:
            raise RuntimeError('Flash Studio window does not accept native file drops.')
        source = root.create_window(0, 0, 1, 1, 0, X.CopyFromParent, X.InputOutput,
                                    X.CopyFromParent, event_mask=X.PropertyChangeMask)
        uri_type = atom('text/uri-list')
        selection = atom('XdndSelection')
        source.set_selection_owner(selection, X.CurrentTime)
        payload = ('\r\n'.join(p.resolve().as_uri() for p in paths) + '\r\n').encode()
        def send(name, data):
            target.send_event(protocol.event.ClientMessage(window=target.id,
                              client_type=atom(name), data=(32, data)), event_mask=0)
            d.flush()
        send('XdndEnter', [source.id, min(5, int(aware.value[0])) << 24, uri_type, 0, 0])
        geometry = target.get_geometry()
        point = root.translate_coords(target, geometry.width // 2, geometry.height // 2)
        position = ((point.x & 0xffff) << 16) | (point.y & 0xffff)
        send('XdndPosition', [source.id, 0, position, X.CurrentTime, atom('XdndActionCopy')])
        deadline = time.monotonic() + timeout
        delivered = False
        dropped = False
        while time.monotonic() < deadline:
            if not d.pending_events():
                select.select([d.fileno()], [], [], min(.2, deadline - time.monotonic()))
                if not d.pending_events():
                    continue
            event = d.next_event()
            if event.type == X.ClientMessage:
                values = event.data[1]
                if event.client_type == atom('XdndStatus') and not dropped:
                    if not values[1] & 1:
                        send('XdndLeave', [source.id, 0, 0, 0, 0])
                        raise RuntimeError('Flash Studio declined the file drop. Switch to Prepare and try again.')
                    send('XdndDrop', [source.id, 0, X.CurrentTime, 0, 0])
                    dropped = True
                elif event.client_type == atom('XdndFinished'):
                    if not delivered:
                        raise RuntimeError('Flash Studio did not request the selected files.')
                    if int(aware.value[0]) >= 5 and not values[1] & 1:
                        raise RuntimeError('Flash Studio reported that the import failed.')
                    return
            elif event.type == X.SelectionRequest:
                requestor = d.create_resource_object('window', event.requestor.id)
                prop = event.property or event.target
                accepted = False
                if event.target == uri_type:
                    requestor.change_property(prop, uri_type, 8, payload)
                    accepted = delivered = True
                elif event.target == atom('TARGETS'):
                    requestor.change_property(prop, Xatom.ATOM, 32, [uri_type, atom('TARGETS')])
                    accepted = True
                requestor.send_event(protocol.event.SelectionNotify(time=event.time,
                                     requestor=requestor.id, selection=event.selection,
                                     target=event.target, property=prop if accepted else X.NONE), event_mask=0)
                d.flush()
        raise RuntimeError('Flash Studio did not finish the import. Close any open dialogs and try again.')
    finally:
        if source is not None:
            source.destroy()
            d.flush()
        d.close()
