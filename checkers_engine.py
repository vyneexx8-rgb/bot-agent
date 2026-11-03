"""Russian-style 8x8 checkers for a friendly two-player Telegram game.

White moves up; black moves down. Men capture backward, kings are flying,
and capture chains are mandatory after a capture starts. Skipping an available
capture is allowed but costs one of the pieces that could have captured
(the informal "fuk" rule). No external dependencies.
"""

SIZE = 8
DIRECTIONS = ((-1, -1), (1, -1), (-1, 1), (1, 1))


def new_board():
    return [('b' if y < 3 else 'w' if y > 4 else '') if (x + y) % 2 else ''
            for y in range(8) for x in range(8)]


def belonging(piece, player):
    return piece.lower() == player


def enemies(piece, other):
    return bool(other) and piece.lower() != other.lower()


def inside(x, y):
    return 0 <= x < 8 and 0 <= y < 8


def captures_at(board, at):
    """Return (destination_index, captured_index) for a single capture."""
    piece = board[at]
    if not piece:
        return []
    x, y = at % 8, at // 8
    found = []
    for dx, dy in DIRECTIONS:
        if piece.islower():
            mx, my, tx, ty = x + dx, y + dy, x + 2*dx, y + 2*dy
            if inside(tx, ty) and enemies(piece, board[my * 8 + mx]) and not board[ty * 8 + tx]:
                found.append((ty * 8 + tx, my * 8 + mx))
        else:
            xx, yy, jumped = x + dx, y + dy, None
            while inside(xx, yy):
                p = board[yy * 8 + xx]
                if p:
                    if jumped is not None or not enemies(piece, p):
                        break
                    jumped = yy * 8 + xx
                elif jumped is not None:
                    found.append((yy * 8 + xx, jumped))
                xx += dx
                yy += dy
    return found


def quiet_at(board, at):
    piece = board[at]
    if not piece:
        return []
    x, y = at % 8, at // 8
    result = []
    for dx, dy in DIRECTIONS:
        if piece.islower() and dy != (-1 if piece == 'w' else 1):
            continue
        xx, yy = x + dx, y + dy
        while inside(xx, yy) and not board[yy * 8 + xx]:
            result.append(yy * 8 + xx)
            if piece.islower():
                break
            xx += dx
            yy += dy
    return result


def available(board, player, chained_at=None):
    """Return {source: [(destination, captured-or-None)]}.

    Initial captures are optional, but a capture *chain* must be completed.
    All destinations are supplied to the UI so the player can choose.
    """
    pieces = ([chained_at] if chained_at is not None else
              [i for i, piece in enumerate(board) if belonging(piece, player)])
    if chained_at is not None:
        if not board[chained_at] or not belonging(board[chained_at], player):
            return {}
        options = captures_at(board, chained_at)
        return {chained_at: options} if options else {}
    result = {}
    for i in pieces:
        cap = captures_at(board, i)
        quiet = [(target, None) for target in quiet_at(board, i)]
        if cap or quiet:
            result[i] = cap + quiet
    return result


def possible_capturers(board, player):
    """Locations of pieces that can capture before a move."""
    return [i for i, piece in enumerate(board)
            if belonging(piece, player) and captures_at(board, i)]

def make_step(board, source, dest, capture=None):
    board = board.copy()
    piece = board[source]
    board[source] = ''
    board[dest] = piece
    if capture is not None:
        board[capture] = ''
    # Promotion is immediate, including halfway through a capture chain.
    if piece == 'w' and dest // 8 == 0:
        board[dest] = 'W'
    if piece == 'b' and dest // 8 == 7:
        board[dest] = 'B'
    return board


def move(state, cell, user_id):
    """Return (changed, description); support selection, fuk and combinations."""
    turn = state['turn']
    expected = state['owner'] if turn == 'w' else state['chat_id']
    if user_id != expected:
        return False, 'Сейчас ход другого игрока.'
    if state.get('finished'):
        return False, 'Партия завершена.'
    board = state['board']
    chain = state.get('chain')
    opts = available(board, turn, chain)
    if not opts:
        state['finished'] = True
        return True, 'Нет доступных ходов. Партия завершена.'
    selected = state.get('selected')
    if cell in opts and chain is None:
        state['selected'] = cell
        return True, 'Шашка выбрана. Теперь нажми зелёную клетку.'
    if selected is None or selected not in opts:
        return False, 'Сначала выбери свою шашку.'
    possible = [captured for dest, captured in opts[selected] if dest == cell]
    if not possible:
        return False, 'Неверная клетка. Выбирай зелёную.'
    captured = possible[0]
    fuk_candidates = possible_capturers(board, turn) if chain is None and captured is None else []
    board = make_step(board, selected, cell, captured)
    state['board'] = board
    if captured is not None:
        continuation = available(board, turn, cell)
        if continuation:
            state['selected'] = cell
            state['chain'] = cell
            state['combo'] = state.get('combo', 0) + 1
            return True, f'Комбо ×{state["combo"]}! Продолжай бить этой шашкой.'
    combo = state.get('combo', 0) + (1 if captured is not None else 0)
    punishment = ''
    if fuk_candidates:
        # Prefer the piece that passed up the capture; otherwise the first
        # eligible capturer in stable board order. This is a house rule.
        lost_at = cell if selected in fuk_candidates else fuk_candidates[0]
        board[lost_at] = ''
        punishment = 'Фук! Пропущено взятие: одна шашка снята. '
    state['chain'] = None
    state['selected'] = None
    state['combo'] = 0
    state['turn'] = 'b' if turn == 'w' else 'w'
    if not available(board, state['turn']):
        state['finished'] = True
        return True, punishment + f'Победа {"белых" if turn == "w" else "чёрных"}! Соперник не может ходить.'
    prefix = f'Комбо из {combo} взятий! ' if combo > 1 else ('Взятие! ' if combo == 1 else '')
    return True, punishment + prefix + ('Ход белых.' if state['turn'] == 'w' else 'Ход чёрных.')
