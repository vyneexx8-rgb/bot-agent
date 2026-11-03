import unittest
import checkers_engine as c

class CheckersV3Tests(unittest.TestCase):
    def test_initial_board(self):
        b = c.new_board()
        self.assertEqual((len(b), b.count('w'), b.count('b')), (64, 12, 12))
        self.assertTrue(c.available(b, 'w'))
        self.assertTrue(c.available(b, 'b'))

    def test_men_capture_backward(self):
        b = ['']*64
        b[3*8+3] = 'w'
        b[4*8+4] = 'b'
        opts = c.available(b, 'w')
        self.assertIn((5*8+5, 4*8+4), opts[27])

    def test_optional_capture(self):
        b = ['']*64
        b[5*8+1] = 'w'
        b[5*8+5] = 'w'
        b[4*8+2] = 'b'
        opts = c.available(b, 'w')
        self.assertIn(41, opts)
        self.assertIn(45, opts)
        self.assertIn((3*8+3, 4*8+2), opts[41])
        self.assertIn((4*8+6, None), opts[45])

    def test_king_long_flying_and_backward(self):
        b = ['']*64
        b[5*8+1] = 'W'
        b[3*8+3] = 'b'
        opts = c.available(b, 'w')
        self.assertTrue(all(capture in opts[41] for capture in [(2*8+4, 3*8+3), (1*8+5, 3*8+3), (0*8+6, 3*8+3)]))
        b = ['']*64; b[4*8+4]='W'
        self.assertIn(7*8+1, c.quiet_at(b, 4*8+4))

    def test_fuk_on_quiet_move_with_other_piece(self):
        b = [''] * 64
        b[41] = 'w'  # Can jump the black piece
        b[45] = 'w'  # Moves quietly instead
        b[34] = 'b'
        b[18] = 'b'  # Keeps the other player able to play
        state = {'board': b, 'turn': 'w', 'owner': 1, 'chat_id': 2,
                 'selected': None, 'chain': None, 'combo': 0, 'finished': False}
        self.assertTrue(c.move(state, 45, 1)[0])
        ok, msg = c.move(state, 38, 1)  # f6 -> g5
        self.assertTrue(ok)
        self.assertIn('Фук', msg)
        self.assertEqual(state['board'][41], '')
        self.assertEqual(state['board'][38], 'w')

    def test_fuk_on_piece_that_could_capture(self):
        b = [''] * 64
        b[41] = 'w'
        b[34] = 'b'
        b[20] = 'b'
        state = {'board': b, 'turn': 'w', 'owner': 1, 'chat_id': 2,
                 'selected': None, 'chain': None, 'combo': 0, 'finished': False}
        self.assertTrue(c.move(state, 41, 1)[0])
        ok, msg = c.move(state, 32, 1)  # quiet move of capturing checker
        self.assertTrue(ok)
        self.assertIn('Фук', msg)
        self.assertEqual(state['board'][32], '')
        self.assertEqual(state['board'][41], '')

    def test_no_fuk_when_capturing(self):
        b = [''] * 64
        b[41] = 'w'
        b[34] = 'b'
        b[20] = 'b'
        state = {'board': b, 'turn': 'w', 'owner': 1, 'chat_id': 2,
                 'selected': None, 'chain': None, 'combo': 0, 'finished': False}
        self.assertTrue(c.move(state, 41, 1)[0])
        ok, msg = c.move(state, 27, 1)
        self.assertTrue(ok)
        self.assertNotIn('Фук', msg)
        self.assertEqual(state['board'][27], 'w')

    def test_chain_capture_same_piece(self):
        b = ['']*64
        b[5*8+5] = 'w'; b[4*8+4] = 'b'; b[2*8+2] = 'b'
        state = {'kind':'chk','board':b,'turn':'w','owner':100,'chat_id':200,
                 'selected':None,'chain':None,'combo':0,'finished':False}
        ok, msg = c.move(state, 45, 100)
        self.assertTrue(ok)
        self.assertEqual(state['selected'], 45)
        ok, msg = c.move(state, 27, 100)
        self.assertTrue(ok)
        self.assertEqual(state['chain'], 27)
        self.assertEqual(state['turn'], 'w')
        ok, msg = c.move(state, 9, 100)
        self.assertTrue(ok)
        self.assertIsNone(state['chain'])
        self.assertEqual(state['turn'], 'b')
        self.assertFalse(any(c in ('b','B') for c in state['board']))

    def test_promotion_during_capture(self):
        b = ['']*64
        b[2*8+2]='w'; b[1*8+3]='b'; b[1*8+5]='b'
        state = {'board':b,'turn':'w','owner':1,'chat_id':2,
                 'selected':None,'chain':None,'combo':0,'finished':False}
        self.assertTrue(c.move(state, 2*8+2, 1)[0])
        self.assertTrue(c.move(state, 0*8+4, 1)[0])
        self.assertEqual(state['board'][4],'W')
        self.assertTrue(state['chain'] is not None)
        self.assertTrue(any(to == 2*8+6 for to, cap in c.available(state['board'],'w',4)[4]))

if __name__ == '__main__': unittest.main()
