import time

import idaapi
import idautils
import idc

# This limits the depth of any individual path, as well as the maximum
# number of paths that will be searched; this is needed for practical
# reasons, as IDBs with tens of thousands of functions take a long time
# to exhaust all possible paths without some practical limitation.
#
# This is global so it's easy to change from the IDAPython prompt.
ALLEYCAT_LIMIT = 10000


class AlleyCatException(Exception):
    pass


class AlleyCat(object):
    """
    Class which resolves code paths. This is where most of the work is done.
    """

    def __init__(self, start, end, quiet=False):
        """
        Class constructor.

        @start - The start address.
        @end   - The end address.

        Returns None.
        """
        global ALLEYCAT_LIMIT
        self.limit = ALLEYCAT_LIMIT
        self.paths = []
        self.quiet = quiet

        # We work backwards via xrefs, so we start at the end and end at the start
        if not self.quiet:
            print('Generating call paths from %s to %s...' % (self._name(end), self._name(start)))
        self._build_paths(start, end)

    @staticmethod
    def _name(ea):
        name = idc.get_name(ea, idaapi.GN_VISIBLE)
        if not name:
            name = idc.get_func_off_str(ea)
            if not name:
                name = '0x%X' % ea
        return name

    def _add_path(self, path):
        if path not in self.paths:
            self.paths.append(path)

    def _build_paths(self, start, end=idc.BADADDR):
        partial_paths = [[start]]

        # Loop while there are still unresolve paths and while all path sizes have not exceeded ALLEYCAT_LIMIT
        while partial_paths and len(self.paths) < self.limit and len(partial_paths) < self.limit:
            # Initialize a unique set of callers for this iteration
            callers = set()

            # Callee is the last entry of the first path in partial paths.
            # The first path list will change as paths are completed and popped from the list.
            callee = partial_paths[0][-1]

            # Find all unique functions that reference the callee, assuming this path has not
            # exceeded ALLEYCAT_LIMIT.
            if len(partial_paths[0]) < self.limit:
                for xref in idautils.XrefsTo(callee):
                    caller = AlleyCatFunctionPaths.get_code_block(xref.frm)
                    if caller and caller.start_ea not in callers:
                        callers.add(caller.start_ea)

            # If there are callers to the callee, remove the callee's current path
            # and insert new ones with the new callers appended.
            if callers:
                base_path = partial_paths.pop(0)
                for caller in callers:

                    # Don't want to loop back on ourselves in the same path
                    if caller in base_path:
                        continue

                    # If we've reached the desired end node, don't go any further down this path
                    if caller == end:
                        self._add_path((base_path + [caller])[::-1])
                    else:
                        partial_paths.append(base_path + [caller])
            # Else, our end node is not in this path, so don't include it in the finished path list.
            elif end not in partial_paths[0]:
                partial_paths.pop(0)
            # If there were no callers then this path has been exhaused and should be
            # popped from the partial path list into the finished path list.
            elif end in partial_paths[0]:
                # Paths start with the end function and end with the start function; reverse it.
                self._add_path(partial_paths.pop(0)[::-1])


class AlleyCatFunctionPaths(AlleyCat):
    def __init__(self, start_ea, end_ea, quiet=False):

        # We work backwards via xrefs, so we start at the end and end at the start
        try:
            start = idaapi.get_func(end_ea).start_ea
        except:
            raise AlleyCatException('Address 0x%X is not part of a function!' % end_ea)

        try:
            end = idaapi.get_func(start_ea).start_ea
        except:
            end = idc.BADADDR

        super(AlleyCatFunctionPaths, self).__init__(start, end, quiet)

    @staticmethod
    def get_code_block(ea):
        return idaapi.get_func(ea)


class AlleyCatCodePaths(AlleyCat):

    def __init__(self, start_ea, end_ea, quiet=False):
        end_func = idaapi.get_func(end_ea)
        start_func = idaapi.get_func(start_ea)

        if not start_func:
            raise AlleyCatException('Address 0x%X is not part of a function!' % start_ea)
        if not end_func:
            raise AlleyCatException('Address 0x%X is not part of a function!' % end_ea)
        if start_func.start_ea != end_func.start_ea:
            raise AlleyCatException('The start and end addresses are not part of the same function!')

        self.func = start_func
        self.blocks = [block for block in idaapi.FlowChart(self.func)]

        # We work backwards via xrefs, so we start at the end and end at the start
        end_block = self._get_code_block(start_ea)
        start_block = self._get_code_block(end_ea)

        if not end_block:
            raise AlleyCatException('Failed to find the code block associated with address 0x%X' % start_ea)
        if not start_block:
            raise AlleyCatException('Failed to find the code block associated with address 0x%X' % end_ea)

        super(AlleyCatCodePaths, self).__init__(start_block.start_ea, end_block.start_ea, quiet)

    def _get_code_block(self, ea):
        for block in self.blocks:
            if block.start_ea <= ea < block.end_ea:
                return block
        return None


# Everything below here is just IDA UI/Plugin stuff ###


# noinspection PyAttributeOutsideInit
class AlleyCatGraphHistory(object):
    """
    Manages include/exclude graph history.
    """

    INCLUDE_ACTION = 0
    EXCLUDE_ACTION = 1

    def __init__(self):
        self.reset()

    def reset(self):
        self.history = []
        self.includes = []
        self.excludes = []
        self.history_index = 0
        self.include_index = 0
        self.exclude_index = 0

    def update_history(self, action):
        if self.excludes and len(self.history) - 1 != self.history_index:
            self.history = self.history[0:self.history_index + 1]
        self.history.append(action)
        self.history_index = len(self.history) - 1

    def add_include(self, obj):
        if self.includes and len(self.includes) - 1 != self.include_index:
            self.includes = self.includes[0:self.include_index + 1]
        self.includes.append(obj)
        self.include_index = len(self.includes) - 1
        self.update_history(self.INCLUDE_ACTION)

    def add_exclude(self, obj):
        if len(self.excludes) - 1 != self.exclude_index:
            self.excludes = self.excludes[0:self.exclude_index + 1]
        self.excludes.append(obj)
        self.exclude_index = len(self.excludes) - 1
        self.update_history(self.EXCLUDE_ACTION)

    def get_includes(self):
        return set(self.includes[0:self.include_index + 1])

    def get_excludes(self):
        return set(self.excludes[0:self.exclude_index + 1])

    def undo(self):
        if self.history:
            if self.history[self.history_index] == self.INCLUDE_ACTION:
                if self.include_index >= 0:
                    self.include_index -= 1
            elif self.history[self.history_index] == self.EXCLUDE_ACTION:
                if self.exclude_index >= 0:
                    self.exclude_index -= 1

            self.history_index -= 1
            if self.history_index < 0:
                self.history_index = 0

    def redo(self):
        self.history_index += 1
        if self.history_index >= len(self.history):
            self.history_index = len(self.history) - 1

        if self.history[self.history_index] == self.INCLUDE_ACTION:
            if self.include_index < len(self.includes) - 1:
                self.include_index += 1
        elif self.history[self.history_index] == self.EXCLUDE_ACTION:
            if self.exclude_index < len(self.excludes) - 1:
                self.exclude_index += 1


class AlleyCatGraph(idaapi.GraphViewer):
    """
    Displays the graph and manages graph actions.
    """

    def __init__(self, results, title='AlleyCat Graph'):
        idaapi.GraphViewer.__init__(self, title)
        self.results = results

        self.nodes_ea2id = {}
        self.nodes_id2ea = {}
        self.edges = {}
        self.end_nodes = []
        self.edge_nodes = []
        self.start_nodes = []

        self.history = AlleyCatGraphHistory()
        self.include_on_click = False
        self.exclude_on_click = False

    # noinspection PyAttributeOutsideInit
    def Show(self):
        """
        Display the graph.

        Returns True on success, False on failure.
        """
        if not idaapi.GraphViewer.Show(self):
            return False
        else:
            self.cmd_undo = self.AddCommand('Undo', '')
            self.cmd_redo = self.AddCommand('Redo', '')
            self.cmd_reset = self.AddCommand('Reset graph', '')
            self.cmd_exclude = self.AddCommand('Exclude node', '')
            self.cmd_include = self.AddCommand('Include node', '')
            self.cmd_unhighlight = self.AddCommand('Temporarily un-highlight all paths', '')
            return True

    def OnRefresh(self):
        # Clear the graph before refreshing
        self.clear()
        self.nodes_ea2id = {}
        self.nodes_id2ea = {}
        self.edges = {}
        self.end_nodes = []
        self.edge_nodes = []
        self.start_nodes = []

        includes = self.history.get_includes()
        excludes = self.history.get_excludes()

        for path in self.results:
            parent_node = None

            # Check to see if this path contains all nodes marked for explicit inclusion
            if (set(path) & includes) != includes:
                continue

            # Check to see if this path contains any nodes marked for explicit exclusion
            if (set(path) & excludes) != set():
                continue

            for ea in path:
                # If this node already exists, use its existing node ID
                if ea in self.nodes_ea2id:
                    this_node = self.nodes_ea2id[ea]
                # Else, add this node to the graph
                else:
                    this_node = self.AddNode(self.get_name_by_ea(ea))
                    self.nodes_ea2id[ea] = this_node
                    self.nodes_id2ea[this_node] = ea

                # If there is a parent node, add an edge between the parent node and this one
                if parent_node is not None:
                    self.AddEdge(parent_node, this_node)
                    if this_node not in self.edges[parent_node]:
                        self.edges[parent_node].append(this_node)

                # Update the parent node for the next loop
                parent_node = this_node
                if parent_node not in self.edges:
                    self.edges[parent_node] = []

                # Highlight this node in the disassembly window
                # self.highlight(ea)

            try:
                # Track the first, last, and next to last nodes in each path for
                # proper colorization in self.OnGetText.
                self.start_nodes.append(self.nodes_ea2id[path[0]])
                self.end_nodes.append(self.nodes_ea2id[path[-1]])
                self.edge_nodes.append(self.nodes_ea2id[path[-2]])
            except:
                pass

        return True

    def OnGetText(self, node_id):
        color = idc.DEFCOLOR

        if node_id in self.edge_nodes:
            color = 0x00ffff
        elif node_id in self.start_nodes:
            color = 0x00ff00
        elif node_id in self.end_nodes:
            color = 0x0000ff

        return self[node_id], color

    def OnHint(self, node_id):
        hint = ''

        try:
            for edge_node in self.edges[node_id]:
                hint += '%s\n' % self[edge_node]
        except:
            pass

        return hint

    def OnCommand(self, cmd_id):
        if self.cmd_undo == cmd_id:
            if self.include_on_click or self.exclude_on_click:
                self.include_on_click = False
                self.exclude_on_click = False
            else:
                self.history.undo()
            self.Refresh()
        elif self.cmd_redo == cmd_id:
            self.history.redo()
            self.Refresh()
        elif self.cmd_include == cmd_id:
            self.include_on_click = True
        elif self.cmd_exclude == cmd_id:
            self.exclude_on_click = True
        elif self.cmd_reset == cmd_id:
            self.include_on_click = False
            self.exclude_on_click = False
            self.history.reset()
            self.Refresh()
        # elif self.cmd_unhighlight == cmd_id:
        #     self.unhighlight_all()

    def OnClick(self, node_id):
        if self.include_on_click:
            self.history.add_include(self.nodes_id2ea[node_id])
            self.include_on_click = False
        elif self.exclude_on_click:
            self.history.add_exclude(self.nodes_id2ea[node_id])
            self.exclude_on_click = False
        self.Refresh()

    def OnDblClick(self, node_id):
        xref_locations = []
        node_ea = self.get_ea_by_name(self[node_id])

        if node_id in self.edges:
            for edge_node_id in self.edges[node_id]:

                edge_node_name = self[edge_node_id]
                edge_node_ea = self.get_ea_by_name(edge_node_name)

                if edge_node_ea != idc.BADADDR:
                    for xref in idautils.XrefsTo(edge_node_ea):
                        # Is the specified node_id the source of this xref?
                        if self.match_xref_source(xref, node_ea):
                            xref_locations.append((xref.frm, edge_node_ea))

        if xref_locations:
            xref_locations.sort()

            print('')
            print('Path Xrefs from %s:' % self[node_id])
            print('-' * 100)
            for (xref_ea, dst_ea) in xref_locations:
                print('%-50s  =>  %s' % (self.get_name_by_ea(xref_ea), self.get_name_by_ea(dst_ea)))
            print('-' * 100)
            print('')

            idc.jumpto(xref_locations[0][0])
        else:
            idc.jumpto(node_ea)

    def OnClose(self):
        # TODO: Add a 'do not ask again' feature?
        # if idc.AskYN(1, "Path nodes have been highlighted in the disassembly window. Undo highlighting?") == 1:
        #     self.unhighlight_all()
        pass

    @staticmethod
    def match_xref_source(xref, source):
        # TODO: This must be modified if support for graphing function blocks is added.
        return (xref.type != idc.fl_F) and (idc.get_func_attr(xref.frm, idc.FUNCATTR_START) == source)

    @staticmethod
    def get_ea_by_name(name):
        """
        Get the address of a location by name.

        @name - Location name

        Returns the address of the named location, or idc.BADADDR on failure.
        """
        # This allows support of the function offset style names (e.g., main+0C)
        # TODO: Is there something in the IDA API that does this already??
        # TODO: AppCall maybe? http://www.hexblog.com/?p=112 -fireundubh
        ea = idc.BADADDR
        if '+' in name:
            (func_name, offset) = name.split('+')
            base_ea = idc.get_name_ea_simple(func_name)
            if base_ea != idc.BADADDR:
                try:
                    ea = base_ea + int(offset, 16)
                except:
                    ea = idc.BADADDR
        else:
            ea = idc.get_name_ea_simple(name)
            if ea == idc.BADADDR:
                try:
                    ea = int(name, 0)
                except:
                    ea = idc.BADADDR
        return ea

    def clear(self):
        # Clears the graph and unhighlights the disassembly
        self.Clear()
        # self.unhighlight_all()

    @staticmethod
    def get_name_by_ea(ea):
        """
        Get the name of the specified address.

        @ea - Address.

        Returns a name for the address, one of idc.get_name, idc.get_func_off_str or 0xXXXXXXXX.
        """
        name = idc.get_name(ea, idaapi.GN_VISIBLE)
        if not name:
            name = idc.get_func_off_str(ea)
            if not name:
                name = '0x%X' % ea
        return name

    @staticmethod
    def colorize_node(ea, color):
        # Colorizes an entire code block
        func = idaapi.get_func(ea)
        if not func:
            return

        for block in idaapi.FlowChart(func):
            if block.start_ea <= ea < block.end_ea:
                ea = block.start_ea
                while ea < block.end_ea:
                    idaapi.set_item_color(ea, color)
                    ea = idc.next_head(ea)
                break

    def highlight(self, ea):
        # Highlights an entire code block
        # self.colorize_node(ea, color=0xFFFBCC)
        pass

    def unhighlight(self, ea):
        # Unhighlights an entire code block
        self.colorize_node(ea, idc.DEFCOLOR)

    def unhighlight_all(self):
        # Unhighlights all code blocks
        for path in self.results:
            for ea in path:
                self.unhighlight(ea)


class AlleycatActionHandlerFindPathsFrom(idaapi.action_handler_t):
    def __init__(self):
        idaapi.action_handler_t.__init__(self)

    def activate(self, ctx):
        target = idapathfinder_t.current_function()

        if not target:
            return 1

        sources = idapathfinder_t.get_user_selected_functions(many=True)
        if not sources:
            return 1

        idapathfinder_t.find_and_plot_paths(sources, [target])
        return 0

    def update(self, ctx):
        return idaapi.AST_ENABLE_ALWAYS


class AlleycatActionHandlerFindPathsTo(idaapi.action_handler_t):
    def __init__(self):
        idaapi.action_handler_t.__init__(self)

    def activate(self, ctx):
        source = idapathfinder_t.current_function()

        if not source:
            return 1

        targets = idapathfinder_t.get_user_selected_functions(many=True)
        if not targets:
            return 1

        idapathfinder_t.find_and_plot_paths([source], targets)
        return 0

    def update(self, ctx):
        return idaapi.AST_ENABLE_ALWAYS


class AlleycatActionHandlerFindPathsToCurrentBlock(idaapi.action_handler_t):
    def __init__(self):
        idaapi.action_handler_t.__init__(self)

    def activate(self, ctx):
        target = idc.here()
        source = idapathfinder_t.current_function()

        if not source:
            return 1

        idapathfinder_t.find_and_plot_paths([source], [target], AlleyCatCodePaths)
        return 0

    def update(self, ctx):
        return idaapi.AST_ENABLE_ALWAYS


# noinspection PyPep8Naming
class idapathfinder_t(idaapi.plugin_t):
    flags = 0
    comment = ''
    help = ''
    wanted_name = 'AlleyCat'
    wanted_hotkey = ''

    def __init__(self):
        self.graph = None
        self.menu_action_find_from = idaapi.action_desc_t('alleycat:find_from', 'Find paths to the current function from...', AlleycatActionHandlerFindPathsFrom())
        self.menu_action_find_to = idaapi.action_desc_t('alleycat:find_to', 'Find paths from the current function to...', AlleycatActionHandlerFindPathsTo())
        self.menu_action_find_current = idaapi.action_desc_t('alleycat:find_current', 'Find paths in the current function to the current code block', AlleycatActionHandlerFindPathsToCurrentBlock())
        idaapi.register_action(self.menu_action_find_from)
        idaapi.register_action(self.menu_action_find_to)
        idaapi.register_action(self.menu_action_find_current)

    # noinspection PyMethodMayBeStatic
    def init(self):
        idaapi.attach_action_to_menu('View/Graphs/Find paths to the current function from...', 'alleycat:find_from', idaapi.SETMENU_APP)
        idaapi.attach_action_to_menu('View/Graphs/Find paths from the current function to...', 'alleycat:find_to', idaapi.SETMENU_APP)
        idaapi.attach_action_to_menu('View/Graphs/Find paths in the current function to the current code block', 'alleycat:find_current', idaapi.SETMENU_APP)
        return idaapi.PLUGIN_KEEP

    # noinspection PyMethodMayBeStatic
    def term(self):
        idaapi.detach_action_from_menu('View/Graphs/Find paths to the current function from...', 'alleycat:find_from')
        idaapi.detach_action_from_menu('View/Graphs/Find paths from the current function to...', 'alleycat:find_to')
        idaapi.detach_action_from_menu('View/Graphs/Find paths in the current function to the current code block', 'alleycat:find_current')
        return None

    def run(self, arg):
        pass

    @staticmethod
    def current_function():
        result = idaapi.get_func(idc.here())
        if result:
            return result.start_ea
        else:
            print('No linear address found at cursor')

    @staticmethod
    def find_and_plot_paths(sources, targets, klass=AlleyCatFunctionPaths):
        results = []

        for target in targets:
            for source in sources:
                s = time.time()
                r = klass(source, target).paths
                e = time.time()
                print('Found %d paths in %f seconds.' % (len(r), (e - s)))

                if r:
                    results += r
                else:
                    name = idc.get_name(target, idaapi.GN_VISIBLE)
                    if not name:
                        name = '0x%X' % target
                    print('No paths found to', name)

        if not results:
            return

        # Be sure to close any previous graph before creating a new one.
        # Failure to do so may crash IDA.
        try:
            idapathfinder_t.graph.Close()
        except:
            pass

        idapathfinder_t.graph = AlleyCatGraph(results, 'Path Graph')
        idapathfinder_t.graph.Show()

    @staticmethod
    def get_user_selected_functions(many=False):
        functions = []
        ea = idc.here()
        try:
            current_function = idc.get_func_attr(ea, idc.FUNCATTR_START)
        except:
            current_function = None

        while True:
            func = idc.choose_func('Select a function and click OK until all functions have been selected. When finished, click Cancel to display the graph.')
            # ChooseFunction automatically jumps to the selected function
            # if the enter key is pressed instead of clicking 'OK'. Annoying.
            if idc.here() != ea:
                idc.jumpto(ea)

            if not func or func == idc.BADADDR or func == current_function:
                break
            elif func not in functions:
                functions.append(func)

            if not many:
                break

        return functions


def PLUGIN_ENTRY():
    return idapathfinder_t()
