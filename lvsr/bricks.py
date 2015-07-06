from theano import tensor

from blocks.bricks import Initializable, Linear, Random, Brick
from blocks.bricks.base import lazy, application
from blocks.bricks.parallel import Fork
from blocks.bricks.recurrent import BaseRecurrent, recurrent
from blocks.bricks.sequence_generators import AbstractReadout, Readout, SoftmaxEmitter
from blocks.utils import dict_union

from lvsr.ops import FSTProbabilitiesOp, FSTTransitionOp

class RecurrentWithFork(Initializable):

    @lazy(allocation=['input_dim'])
    def __init__(self, recurrent, input_dim, **kwargs):
        super(RecurrentWithFork, self).__init__(**kwargs)
        self.recurrent = recurrent
        self.input_dim = input_dim
        self.fork = Fork(
            [name for name in self.recurrent.sequences
             if name != 'mask'],
             prototype=Linear())
        self.children = [recurrent.brick, self.fork]

    def _push_allocation_config(self):
        self.fork.input_dim = self.input_dim
        self.fork.output_dims = [self.recurrent.brick.get_dim(name)
                                 for name in self.fork.output_names]

    @application(inputs=['input_', 'mask'])
    def apply(self, input_, mask=None, **kwargs):
        return self.recurrent(
            mask=mask, **dict_union(self.fork.apply(input_, as_dict=True),
                                    kwargs))

    @apply.property('outputs')
    def apply_outputs(self):
        return self.recurrent.states


class FSTTransition(BaseRecurrent, Initializable):
    def __init__(self, fst, remap_table, **kwargs):
        """Wrap FST in a recurrent brick.

        Parameters
        ----------
        fst : FST instance
        remap_table : dict
            Maps neutral network characters to FST characters.

        """
        super(FSTTransition, self).__init__(**kwargs)
        self.fst = fst
        self.transition = FSTTransitionOp(fst, remap_table)
        self.probability_computer = FSTProbabilitiesOp(fst, remap_table)
        self.out_dim = len(remap_table)

    @recurrent(sequences=['inputs', 'mask'],
               states=['states', 'logprobs'],
               outputs=['states', 'logprobs'], contexts=[])
    def apply(self, inputs, states=None, logprobs=None,
              mask=None):
        new_states = self.transition(states, inputs)
        if mask:
            new_states = tensor.cast(mask * new_states +
                                     (1. - mask) * states, 'int64')
        logprobs = self.probability_computer(states)
        return new_states, logprobs

    @application(outputs=['states', 'logprobs'])
    def initial_states(self, batch_size, *args, **kwargs):
        return (tensor.ones((batch_size,), dtype='int64') * self.fst.fst.start,
                tensor.zeros((batch_size, self.out_dim)))

    def get_dim(self, name):
        if name == 'states':
            return 0
        if name == 'logprobs':
            return self.out_dim
        if name == 'inputs':
            return 0
        return super(FSTTransition, self).get_dim(name)


class ShallowFusionReadout(Readout):
    def __init__(self, lm_weights_name, beta=1, **kwargs):
        super(ShallowFusionReadout, self).__init__(**kwargs)
        self.lm_weights_name = lm_weights_name
        self.beta = beta

    @application
    def readout(self, **kwargs):
        lm_probs = tensor.exp(-kwargs[self.lm_weights_name])
        return (super(ShallowFusionReadout, self).readout(**kwargs) +
                self.beta * lm_probs)

