import math
import tensorflow as tf

from tensorflow.python.ops import random_ops


def orthogonal_regularizer(scale):
    def ortho_reg(w):
        _, _, _, c = w.get_shape().as_list()

        w = tf.reshape(w, [-1, c])

        identity = tf.eye(c)
        w_transpose = tf.transpose(w)
        w_mul = tf.matmul(w_transpose, w)
        reg = tf.subtract(w_mul, identity)

        ortho_loss = tf.nn.l2_loss(reg)
        return scale * ortho_loss

    return ortho_reg


def orthogonal_regularizer_fully(scale):
    def ortho_reg_fully(w):
        _, c = w.get_shape().as_list()

        identity = tf.eye(c)
        w_transpose = tf.transpose(w)
        w_mul = tf.matmul(w_transpose, w)
        reg = tf.subtract(w_mul, identity)

        ortho_loss = tf.nn.l2_loss(reg)
        return scale * ortho_loss

    return ortho_reg_fully


def variance_scaling_initializer(factor: float = 2., scale_factor: float = .1,
                                 mode: str = "FAN_AVG", uniform: bool = False,
                                 seed: int = 13371337, dtype=tf.float32):
    def _initializer(shape, dtype=dtype, partition_info=None):
        del partition_info

        if shape:
            fan_in = float(shape[-2]) if len(shape) > 1 else float(shape[-1])
            fan_out = float(shape[-1])
        else:
            fan_in = 1.
            fan_out = 1.

        for dim in shape[:-2]:
            fan_in *= float(dim)
            fan_out *= float(dim)

        if mode == 'FAN_IN':
            n = fan_in
        elif mode == 'FAN_OUT':
            n = fan_out
        else:  # mode == 'FAN_AVG':
            n = (fan_in + fan_out) / 2.

        if uniform:
            limit = math.sqrt(3.0 * factor / n)
            _init = random_ops.random_uniform(shape, -limit, limit,
                                              dtype, seed=seed)
        else:
            trunc_stddev = math.sqrt(1.3 * factor / n)
            _init = random_ops.truncated_normal(shape, 0., trunc_stddev,
                                                dtype, seed=seed)
        return _init * scale_factor

    return _initializer


def conv2d(x,
           channels: int,
           kernel: int = 4, stride: int = 2, pad: int = 0, dilation_rate: int = 1,
           pad_type: str = "zero", use_bias: bool = True, sn: bool = True,
           scope: str = "conv2d_0"):
    with tf.variable_scope(scope):
        if pad > 0:
            h = x.get_shape().as_list()[1]
            if h % stride == 0:
                pad *= 2
            else:
                pad = max(kernel - (h % stride), 0)

            pad_top = pad // 2
            pad_bottom = pad - pad_top
            pad_left = pad // 2
            pad_right = pad - pad_left

            if pad_type == "zero":
                x = tf.pad(x, [[0, 0], [pad_top, pad_bottom], [pad_left, pad_right], [0, 0]])
            if pad_type == "reflect":
                x = tf.pad(x, [[0, 0], [pad_top, pad_bottom], [pad_left, pad_right], [0, 0]], mode="REFLECT")

        if sn:
            if scope.__contains__("generator"):
                w = tf.get_variable("kernel",
                                    shape=[kernel, kernel, x.get_shape()[-1], channels],
                                    initializer=w_init,
                                    regularizer=w_reg)
            else:
                w = tf.get_variable("kernel",
                                    shape=[kernel, kernel, x.get_shape()[-1], channels],
                                    initializer=w_init,
                                    regularizer=None)

            x = tf.nn.conv2d(input=x, filter=spectral_norm(w), dilations=[1, dilation_rate, dilation_rate, 1],
                             strides=[1, stride, stride, 1], padding="VALID")
            if use_bias:
                bias = tf.get_variable("bias", [channels], initializer=tf.constant_initializer(.0))
                x = tf.nn.bias_add(x, bias)
        else:
            if scope.__contains__("generator"):
                x = tf.layers.conv2d(inputs=x, filters=channels,
                                     kernel_size=kernel, kernel_initializer=w_init,
                                     kernel_regularizer=w_reg, dilation_rate=dilation_rate,
                                     strides=stride, use_bias=use_bias)
            else:
                x = tf.layers.conv2d(inputs=x, filters=channels,
                                     kernel_size=kernel, kernel_initializer=w_init,
                                     kernel_regularizer=None, dilation_rate=dilation_rate,
                                     strides=stride, use_bias=use_bias)
        return x


def gate_conv2d(x_in,
                channels: int, kernel: int = 3, stride: int = 1, pad: int = 0, rate: int = 1,
                padding: str = "SAME",
                use_lrn: bool = True, sn: bool = True,
                scope: str = "gated_conv2d_0"):
    with tf.variable_scope(scope):
        assert padding in ["SYMMETRIC", "SAME", "REFELECT"]
        if padding == "SYMMETRIC" or padding == "REFELECT":
            p = int(rate * (kernel - 1) / 2)
            x_in = tf.pad(x_in, [[0, 0], [p, p], [p, p], [0, 0]], mode=padding)
            padding = "VALID"

        x = tf.layers.conv2d(
            x_in, channels, kernel, stride, dilation_rate=rate,
            activation=tf.nn.sigmoid, padding=padding)

        if use_lrn:
            x = tf.nn.lrn(x, bias=5e-5)

        x = tf.nn.leaky_relu(x, alpha=.2)

        g = tf.layers.conv2d(
            x_in, channels, kernel, stride, dilation_rate=rate,
            activation=tf.nn.sigmoid, padding=padding)

        x = tf.multiply(x, g)
        return x, g


def deconv2d(x,
             channels: int,
             kernel: int = 4, stride: int = 2, padding: str = "SAME",
             use_bias: bool = True, sn: bool = True,
             scope: str = "deconv2d_0"):
    with tf.variable_scope(scope):
        x_shape = x.get_shape().as_list()

        if padding == "SAME":
            output_shape = [x_shape[0], x_shape[1] * stride, x_shape[2] * stride, channels]

        else:
            output_shape = [x_shape[0], x_shape[1] * stride + max(kernel - stride, 0),
                            x_shape[2] * stride + max(kernel - stride, 0), channels]

        if sn:
            w = tf.get_variable("kernel", shape=[kernel, kernel, channels, x.get_shape()[-1]],
                                initializer=w_init, regularizer=w_reg)
            x = tf.nn.conv2d_transpose(x, filter=spectral_norm(w), output_shape=output_shape,
                                       strides=[1, stride, stride, 1], padding=padding)

            if use_bias:
                bias = tf.get_variable("bias", [channels], initializer=tf.constant_initializer(0.0))
                x = tf.nn.bias_add(x, bias)
        else:
            x = tf.layers.conv2d_transpose(inputs=x, filters=channels,
                                           kernel_size=kernel, kernel_initializer=w_init,
                                           kernel_regularizer=w_reg,
                                           strides=stride, padding=padding, use_bias=use_bias)
        return x


def gate_deconv2d(x_in,
                  channels: int, kernel: int = 5, stride: int = 2,
                  padding: str = "SAME",
                  scope: str = "gated_deconv2d_0"):
    with tf.variable_scope(scope):
        x = tf.layers.conv2d_transpose(
            x_in, channels, kernel, stride,
            activation=tf.nn.leaky_relu, padding=padding)

        g = tf.layers.conv2d_transpose(
            x_in, channels, kernel, stride,
            activation=tf.nn.sigmoid, padding=padding)

        x = tf.multiply(x, g)
        return x, g


def dense(x, units: int,
          use_bias: bool = True, sn: bool = True,
          scope: str = "dense_0"):
    with tf.variable_scope(scope):
        x = flatten(x)
        shape = x.get_shape().as_list()
        channels = shape[-1]

        if sn:
            if scope.__contains__("generator"):
                w = tf.get_variable("kernel", [channels, units], tf.float32,
                                    initializer=w_init, regularizer=w_reg_fully)
            else:
                w = tf.get_variable("kernel", [channels, units], tf.float32,
                                    initializer=w_init, regularizer=None)

            if use_bias:
                bias = tf.get_variable("bias", [units],
                                       initializer=tf.constant_initializer(.0))

                x = tf.matmul(x, spectral_norm(w)) + bias
            else:
                x = tf.matmul(x, spectral_norm(w))

        else:
            if scope.__contains__("generator"):
                x = tf.layers.dense(x, units=units, kernel_initializer=w_init,
                                    kernel_regularizer=w_reg_fully, use_bias=use_bias)
            else:
                x = tf.layers.dense(x, units=units, kernel_initializer=w_init,
                                    kernel_regularizer=None, use_bias=use_bias)
        return x


def batch_norm(x,
               is_training: bool = True,
               scope: str = "batch_norm"):
    return tf.layers.batch_normalization(x,
                                         momentum=.9,
                                         epsilon=1.1e-5,
                                         training=is_training,
                                         name=scope)


def flatten(x):
    return tf.layers.flatten(x)


def hw_flatten(x):
    return tf.reshape(x, shape=[x.shape[0], -1, x.shape[-1]])


def global_avg_pooling_2d(x):
    return tf.reduce_mean(x, axis=[1, 2])


def global_sum_pooling_2d(x):
    return tf.reduce_sum(x, axis=[1, 2])


def max_pooling_2d(x):
    return tf.layers.max_pooling2d(x, pool_size=2, strides=2, padding="SAME")


def up_sample_2d(x, scale_factor: int = 2):
    _, h, w, _ = x.get_shape().as_list()
    return tf.image.resize_nearest_neighbor(x, size=(h * scale_factor, w * scale_factor))


def spectral_norm(w, iteration: int = 1):
    w_shape = w.shape.as_list()

    w = tf.reshape(w, [-1, w_shape[-1]])
    u = tf.get_variable("u", [1, w_shape[-1]],
                        initializer=tf.random_normal_initializer(), trainable=False)

    u_hat = u
    v_hat = None
    for i in range(iteration):
        v_ = tf.matmul(u_hat, tf.transpose(w))
        v_hat = tf.nn.l2_normalize(v_)

        u_ = tf.matmul(v_hat, w)
        u_hat = tf.nn.l2_normalize(u_)

    u_hat = tf.stop_gradient(u_hat)
    v_hat = tf.stop_gradient(v_hat)

    sigma = tf.matmul(tf.matmul(v_hat, w), tf.transpose(u_hat))

    with tf.control_dependencies([u.assign(u_hat)]):
        w_norm = w / sigma
        w_norm = tf.reshape(w_norm, w_shape)
    return w_norm


def he_uniform_initializer(factor: float = 3., scale_factor: float = .1):
    return variance_scaling_initializer(factor=factor, scale_factor=scale_factor)


def orth_regularizer(scale: float = 1e-4):
    return orthogonal_regularizer(scale)


def orth_regularizer_fully(scale: float = 1e-4):
    return orthogonal_regularizer_fully(scale)