"""
Module for FNN-Autoencoders.
"""

import sys      # Path to find PyEDDL library in PyCOMPSs container
sys.path.append('/usr/local/miniconda3/lib/python3.8/site-packages')
from pyeddl import eddl
from pyeddl.tensor import Tensor
import numpy as np
from pycompss.api.task import task
from pycompss.api.parameter import INOUT, IN
from .reduction import Reduction

class AE_EDDL(Reduction):
    """
    Feed-Forward AutoEncoder class (AE)

    :param list layers_encoder: ordered list with the number of neurons of
        each hidden layer for the encoder
    :param list layers_decoder: ordered list with the number of neurons of
        each hidden layer for the decoder
    :param pyeddl.eddl.activation function_encoder: activation function
        at each layer for the encoder, except for the output layer at with
        Identity is considered by default.  A single activation function can
        be passed or a list of them of length equal to the number of hidden
        layers.
    :param pyeddl.eddl.activation function_decoder: activation function
        at each layer for the decoder, except for the output layer at with
        Identity is considered by default.  A single activation function can
        be passed or a list of them of length equal to the number of hidden
        layers.
    :param list stop_training: list with the maximum number of training
        iterations (int) and/or the desired tolerance on the training loss
        (float).
    :param int pyeddl.eddl batch_size: size of data batches used for
        training the network.
    :param str pyeddl.eddl loss: loss definition (Mean Squared if not
        given).
    :param str pyeddl.eddl metric: metric definition (Mean Squared if not
        given).
    :param pyeddl.eddl optimizer: the eddl class implementing optimizer
        Default value is `Adam` optimizer.
    :param float lr: the learning rate. Default is 0.001.
    :param cs: type and number of the computing service. Default is
        eddl.CS_CPU().
    :param int training_type: 1 for Coarse training, 2 Fine training_1
        3 for Fine training_2. Default is 2.

    :Example:
        >>> from ezyrb import AE_EDDL
        >>> import pyeddl.eddl as eddl
        >>> from pyeddl.tensor import Tensor
        >>> f = eddl.Tanh
        >>> low_dim = 5
        >>> optim = eddl.adam
        >>> ae = AE_EDDL([400, low_dim], [low_dim, 400], f(), f(), 2000)
        >>> # or ...
        >>> ae = AE_EDDL([400, 10, 10, low_dim], [low_dim, 400], f(), f(), 1e-5,
        >>>          optimizer=optim)
        >>> ae.fit(snapshots)
        >>> reduced_snapshots = ae.reduce(snapshots)
        >>> expanded_snapshots = ae.expand(reduced_snapshots)
    """
    def __init__(self,
                 layers_encoder,
                 layers_decoder,
                 function_encoder,
                 function_decoder,
                 stop_training,
                 batch_size,
                 loss=None,
                 metric=None,
                 optimizer=eddl.adam,
                 lr=0.001,
                 cs = eddl.CS_CPU,
                 training_type = 2):

        if loss is None:
            loss = "mean_squared_error"
        if metric is None:
            metric = "mean_squared_error"

        if not isinstance(function_encoder, list):
            # Single activation function passed
            function_encoder = [function_encoder] * (len(layers_encoder))
        if not isinstance(function_decoder, list):
            # Single activation function passed
            function_decoder = [function_decoder] * (len(layers_decoder))

        if not isinstance(stop_training, list):
            stop_training = [stop_training]

        self.layers_encoder = layers_encoder
        self.layers_decoder = layers_decoder
        self.function_encoder = function_encoder
        self.function_decoder = function_decoder
        self.loss = loss
        self.metric = metric

        self.stop_training = stop_training
        self.batch_size = batch_size
        self.loss_trend = []
        self.metric_trend = []
        self.encoder = None
        self.decoder = None
        self.decoder2 = None
        self.optimizer = optimizer
        self.model_Autoencoder = None
        self.model_Decoder = None
        self.lr = lr
        self.cs = cs
        self.training_type = training_type

    def _build_model(self, values):
        """
        Build the PyEDDL model.

        Considering the number of neurons per layer (self.layers), a
        feed-forward NN is defined:
            - activation function from layer i>=0 to layer i+1:
              self.function[i]; activation function at the output layer:
              Identity (by default).

        :param numpy.ndarray values: the set values one wants to reduce.
        """
        layers_encoder = self.layers_encoder.copy()
        layers_encoder.insert(0, values.shape[1])

        layers_decoder = self.layers_decoder.copy()
        layers_decoder.append(values.shape[1])

        def EncoderBlock(layer):
            for i in range(1, len(layers_encoder)-1):
                layer = self.function_encoder[i](
                    eddl.Dense(layer, layers_encoder[i]))
            layer = eddl.Dense(layer, layers_encoder[-1])
            return layer

        def DecoderBlock(layer):
            for i in range(1, len(layers_decoder)-1):
                layer = self.function_encoder[i](
                    eddl.Dense(layer, layers_decoder[i]))
            layer = eddl.Dense(layer, layers_decoder[-1])
            return layer    
        #-----------------------------------------------------------------------
        # Define network1
        in1 = eddl.Input([layers_encoder[0]])
        self.encoder = EncoderBlock(in1)
        self.decoder = DecoderBlock(self.encoder)
        self.model_Autoencoder = eddl.Model([in1], [self.decoder])

        eddl.build(
            self.model_Autoencoder,
            self.optimizer(self.lr),
            [self.loss ],
            [self.metric],
            self.cs(mem="low_mem")
        )
        #-----------------------------------------------------------------------
        # Define network2
        in2 = eddl.Input([layers_decoder[0]])
        self.decoder2 = DecoderBlock(in2)
        self.model_Decoder = eddl.Model([in2], [self.decoder2])
    
        eddl.build(
            self.model_Decoder,
            self.optimizer(self.lr),
            [self.loss ],
            [self.metric],
            self.cs(mem="low_mem")
        )
        #-----------------------------------------------------------------------
        eddl.summary(self.model_Autoencoder)
        eddl.plot(self.model_Autoencoder, "Autoencoder.pdf")
        eddl.summary(self.model_Decoder)
        eddl.plot(self.model_Decoder, "Decoder.pdf")
        #-----------------------------------------------------------------------

    @task(target_direction=INOUT)
    def fit(self, values):
        """
        Build the AE given 'values' and perform training.

        Training procedure information:
            -  optimizer: Adam's method with default parameters.
            -  loss: self.loss (if none, the Mean Squared Loss is set by
               default).
            -  stopping criterion: the fulfillment of the requested tolerance
               on the training loss compatibly with the prescribed budget of
               training iterations (if type(self.stop_training) is list); if
               type(self.stop_training) is int or type(self.stop_training) is
               float, only the number of maximum iterations or the accuracy
               level on the training loss is considered as the stopping rule,
               respectively.

        :param numpy.ndarray values: the (training) values in the points.
        """
        values = values.T
        values = Tensor.fromarray(values) # Numpy array to EDDL.Tensor
        self._build_model(values)
        #-----------------------------------------------------------------------
        if self.training_type == 1:
            print('Coarse training:')
            n_epoch =1
            flag = True
            while flag:
                print('Iteration number: ',n_epoch)
                eddl.fit(self.model_Autoencoder, [values], [values],
                    self.batch_size, 1)
                losses = eddl.get_losses(self.model_Autoencoder)
                metrics = eddl.get_metrics(self.model_Autoencoder)
                self.loss_trend.append(losses)
                self.metric_trend.append(metrics)

                for criteria in self.stop_training:
                    if isinstance(criteria, int):
                        # stop criteria is an integer
                        if n_epoch == criteria:
                            flag = False
                    elif isinstance(criteria, float):
                        # stop criteria is float
                        if losses[0] < criteria:
                            flag = False
                n_epoch += 1
        #-----------------------------------------------------------------------
        elif self.training_type == 2:
            print('Fine training_1:')
            s = values.shape
            num_batches = s[0] // self.batch_size
            xbatch = Tensor([self.batch_size, 3067])
            n_epoch =1
            flag = True
            while flag:
                eddl.reset_loss(self.model_Autoencoder)
                for j in range(num_batches):
                    # 1) using next_batch
                    eddl.next_batch([values], [xbatch])
                    eddl.train_batch(self.model_Autoencoder, [xbatch], [xbatch])

                losses = eddl.get_losses(self.model_Autoencoder)
                metrics = eddl.get_metrics(self.model_Autoencoder)
                self.loss_trend.append(losses)
                self.metric_trend.append(metrics)
                print("Epoch %d (%d batches)" % (n_epoch, num_batches))
                for l, m in zip(losses, metrics):
                    print("Loss: %.6f\tMetric: %.6f" % (l, m))
                
                for criteria in self.stop_training:
                    if isinstance(criteria, int):
                        # stop criteria is an integer
                        if n_epoch == criteria:
                            flag = False
                    elif isinstance(criteria, float):
                        # stop criteria is float
                        if losses[0] < criteria:
                            flag = False
                n_epoch += 1
        #-----------------------------------------------------------------------
        elif self.training_type == 3:
            print('Fine training_2:')
            s = values.shape
            num_batches = s[0] // self.batch_size
            n_epoch =1
            flag = True
            while flag:
                eddl.reset_loss(self.model_Autoencoder)
                for j in range(num_batches):
                    # 2) using samples indices
                    indices = np.random.randint(0, s[0], self.batch_size)
                    eddl.train_batch(self.model_Autoencoder, [values], [values],
                        indices)

                losses = eddl.get_losses(self.model_Autoencoder)
                metrics = eddl.get_metrics(self.model_Autoencoder)
                self.loss_trend.append(losses)
                self.metric_trend.append(metrics)
                print("Epoch {} ({} batches)".format(n_epoch, num_batches))
                for l, m in zip(losses, metrics):
                    print("Loss: %.6f\tMetric: %.6f" % (l, m))
                
                for criteria in self.stop_training:
                    if isinstance(criteria, int):
                        # stop criteria is an integer
                        if n_epoch == criteria:
                            flag = False
                    elif isinstance(criteria, float):
                        # stop criteria is float
                        if losses[0] < criteria:
                            flag = False
                n_epoch += 1
        #-----------------------------------------------------------------------
        # Copy parameters from model_Autoencoder to model_Decoder
        # munber of Decoder layers = (n) hidden layers + (n-1) activation layers
        num_hidden = len(self.layers_encoder)
        num_lay_deccoder = 2*num_hidden -1
        decoder_parameters = eddl.get_parameters(self.model_Autoencoder,
            True)[-num_lay_deccoder:]
        # insertc empty parameter for the new input layer of decoder
        decoder_parameters.insert(0,[])
        eddl.set_parameters(self.model_Decoder, decoder_parameters)
        
        ## For debugging
        # for i in decoder_parameters:
        #     print(len(i))

    @task(returns=np.ndarray, target_direction=IN)
    def transform(self, X, scaler_red):
        """
        Reduces the given snapshots.

        :param numpy.ndarray X: the input snapshots matrix (stored by column).
        """
        X = Tensor.fromarray(X.T) # Numpy array to EDDL.Tensor
        # One prediction for the fitted model(1 forward pass after training)
        eddl.predict(self.model_Autoencoder, [X])
        g = eddl.getOutput(self.encoder)
        reduced_output = (Tensor.getdata(g).T).T # EDDL.Tensor to Numpy array
        if scaler_red:
            reduced_output = scaler_red.fit_transform(reduced_output)
        
        ## For debugging
        # u = eddl.getOutput(self.decoder)
        # print('Latent sapce info.:')
        # g.info()
        # print('Expansion sapce info.:')
        # u.info()

        return reduced_output

    @task(returns=np.ndarray, target_direction=IN)
    def inverse_transform(self, g, database):
        """
        Projects a reduced to full order solution.

        :param: numpy.ndarray g the latent variables.
        """
        g = Tensor.fromarray(g.T) # Numpy array to EDDL.Tensor
        # One forward pass for the new decoder (without training, parameters
        # copied from the trained autoencoder)
        eddl.forward(self.model_Decoder, [g])
        u = eddl.getOutput(self.decoder2)
        
        ## For debugging
        # print('Before expansion', g.shape)
        # print('Before expansion', u.shape)

        predicted_sol = Tensor.getdata(u).T # EDDL.Tensor to Numpy array
        
        if database and database.scaler_snapshots:
            predicted_sol = database.scaler_snapshots.inverse_transform(
                    predicted_sol.T).T

        if 1 in predicted_sol.shape:
            predicted_sol = predicted_sol.ravel()
        else:
            predicted_sol = predicted_sol.T
        
        return predicted_sol

    def reduce(self, X, scaler_red):
        """
        Reduces the given snapshots.

        :param numpy.ndarray X: the input snapshots matrix (stored by column).

        .. note::

            Same as `transform`. Kept for backward compatibility.
        """
        return self.transform(X, scaler_red)

    def expand(self, g, database):
        """
        Projects a reduced to full order solution.

        :param: numpy.ndarray g the latent variables.

        .. note::

            Same as `inverse_transform`. Kept for backward compatibility.
        """
        return self.inverse_transform(g, database)