# adapted from https://github.com/keras-team/keras-applications
import logging

import numpy as np
import tensorflow as tf

from .layers import (conv,
                     basic_res_block,
                     bottleneck_res_block,
                     downsample)
from .utils import (add_summaries,
                    crop_spatial_temporal,
                    get_number_of_tf_variables,
                    global_average_pool)

logger = logging.getLogger(__name__)


def block_rn_v2(net, num_fmaps, num_fmaps_out,
                activation='relu', padding='same',
                strides=1,
                is_training=None,
                use_batchnorm=False,
                is_first_block=False,
                use_bottleneck=True,
                use_dropout=False,
                conv_shortcut=False,
                make_iso=False,
                merge_time_voxel_size=None,
                name='block',
                fov=(1, 1, 1),
                voxel_size=(1, 1, 1)):
    """A residual block.
    # Arguments
        net: input tensor.
        num_fmaps:
            The number of feature maps to use in inner convolution.
        num_fmaps_out:
            The number of feature maps to output.
        strides:
            default 1, stride of the first layer.
        is_training:
            Boolean or tf.placeholder to set batchnorm and dropout to
            training or test mode
        use_batchnorm:
            Whether to use batch norm layers after convolution
        is_first_block:
            Whether this is the first block in the network
        use_bottleneck:
            use bottleneck structure or simple res blocks
            (for smaller networks)
        conv_shortcut:
            use convolution shortcut if True, otherwise identity shortcut.
        make_iso:
            For anisotropic 3d data, don't downsample z in the beginning,
            until voxel_size is roughly isotropic
        name: string, block label.
        fov:
            Field of view of fmaps_in, in physical units.
        voxel_size:
            Voxel size of the input feature maps. Used to compute the voxel
            size of the output.
    # Returns
        Output tensor for the residual block.
    """
    shape = net.get_shape().as_list()
    num_fmaps_in = shape[1]

    if padding.lower() == 'same' and len(shape) == 6 and \
       merge_time_voxel_size is not None and \
       voxel_size[-1] >= merge_time_voxel_size:
        net, fov = conv(
            net, num_fmaps_in, [shape[2], 1, 1, 1],
            activation=activation,
            padding="valid",
            strides=1,
            name=name + '_remove_temp')
        shape = net.get_shape().as_list()

    if isinstance(strides, int):
        strides = [strides]*(len(shape) - 2)

    if make_iso and len(shape) > 4 and \
       strides[-1] > 1 and shape[-3] * 2 <= shape[-1]:
        strides[-3] = 1

    if len(shape) == 6:
        strides[0] = 1

    if conv_shortcut:
        shortcut, fov = conv(
            net, num_fmaps_out, 1,
            activation=None,
            padding=padding,
            strides=strides,
            name=name + '_conv_shortcut')
        voxel_size = np.array(strides)[-len(voxel_size):] * voxel_size
    else:
        if np.any(np.array(strides) > 1):
            shortcut, voxel_size = downsample(
                net, factors=1, strides=strides,
                name=name+'_down_shortcut', voxel_size=voxel_size)
        else:
            shortcut = tf.identity(net, name=name + "plain_shortcut")
    logger.info("%s", shortcut)
    logger.info("current voxel size: %s", voxel_size)

    if use_bottleneck:
        block_fn = bottleneck_res_block
    else:
        block_fn = basic_res_block

    logger.info("used strides: %s", strides)
    net, fov = block_fn(net,
                        num_fmaps,
                        num_fmaps_out,
                        activation=activation,
                        padding=padding,
                        strides=strides,
                        is_training=is_training,
                        use_dropout=use_dropout,
                        use_batchnorm=use_batchnorm,
                        is_first_block=is_first_block,
                        name=name,
                        fov=fov, voxel_size=voxel_size)

    shortcut = crop_spatial_temporal(shortcut,
                                     net.get_shape().as_list())
    logger.info("%s", net)
    net = tf.add(net, shortcut, name=name + '_out')
    logger.info("%s", net)

    return net, fov, voxel_size


def stack_rn_v2(net, num_fmaps, num_blocks,
                activation='relu', padding='same',
                stride1=2,
                is_training=None,
                use_batchnorm=False,
                is_first_block=False,
                use_bottleneck=False,
                use_dropout=False,
                make_iso=False,
                merge_time_voxel_size=None,
                name=None,
                fov=(1, 1, 1),
                voxel_size=(1, 1, 1)):
    """A set of stacked residual blocks.
    Args:
        net:
            input tensor.
        num_fmaps:
            integer, number of filters in block.
        num_blocks:
            integer, blocks in the stacked blocks.
        stride1:
            default 2, stride in the conv shortcut and the inner convolution.
        is_training:
            A boolean or placeholder tensor indicating whether or not the
            network is training. Will use dropout and batch norm when
            this is true, but not when false.
        use_batchnorm:
            Whether to use batch norm layers after convolution
        is_first_block:
            Whether this is the first block in the network
        use_bottleneck:
            use bottleneck structure in residual block
        make_iso:
            For anisotropic 3d data, don't downsample z in the beginning,
            until voxel_size is roughly isotropic
        name: string, stack label.
    Returns:
        Output tensor for the stacked blocks.
    """
    if use_bottleneck:
        num_fmaps_out = 4 * num_fmaps
        # strides_first = 1
        # strides_last = stride1
    else:
        num_fmaps_out = num_fmaps
        # strides_first = stride1
        # strides_last = 1

    net, fov, voxel_size = block_rn_v2(
        net, num_fmaps, num_fmaps_out,
        activation=activation, padding=padding,
        # strides=strides_first,
        is_training=is_training,
        use_batchnorm=use_batchnorm,
        is_first_block=is_first_block,
        use_bottleneck=use_bottleneck,
        use_dropout=use_dropout,
        conv_shortcut=True,
        make_iso=make_iso,
        merge_time_voxel_size=merge_time_voxel_size,
        name=name + '_block1',
        fov=fov, voxel_size=voxel_size)

    for i in range(2, num_blocks):
            net, fov, voxel_size = block_rn_v2(
                net, num_fmaps, num_fmaps_out,
                activation=activation,
                padding=padding,
                is_training=is_training,
                use_batchnorm=use_batchnorm,
                use_bottleneck=use_bottleneck,
                use_dropout=use_dropout,
                make_iso=make_iso,
                merge_time_voxel_size=merge_time_voxel_size,
                name=name + '_block' + str(i),
                fov=fov, voxel_size=voxel_size)

    net, fov, voxel_size = block_rn_v2(
        net, num_fmaps, num_fmaps_out,
        activation=activation, padding=padding,
        # strides=strides_last,
        strides=stride1,
        is_training=is_training,
        use_batchnorm=use_batchnorm,
        use_bottleneck=use_bottleneck,
        use_dropout=use_dropout,
        make_iso=make_iso,
        merge_time_voxel_size=merge_time_voxel_size,
        name=name + '_block' + str(num_blocks),
        fov=fov, voxel_size=voxel_size)

    return net, fov, voxel_size


def resnet(fmaps_in,
           *,  # this indicates that all following arguments are keyword arguments
           num_classes,
           resnet_size=None,
           num_blocks=None,
           use_bottleneck=None,
           activation='relu',
           padding='same',
           num_fmaps=[64, 128, 256, 512],
           make_iso=False,
           merge_time_voxel_size=None,
           is_training=None,
           use_batchnorm=False,
           use_conv4d=False,
           use_dropout=False,
           voxel_size=(1, 1, 1)):
    ''' Create a ResNet:
    '''
    logger.info("Creating ResNet")
    num_var_start = get_number_of_tf_variables()


    def stack_fn(net, fov, voxel_size):
        # num_fmaps = [16, 32, 64, 96]
        # num_fmaps = [64, 128, 256, 512]
        for i, nb in enumerate(num_blocks):
            is_first_block = i == 0
            is_last_block = i == (len(num_blocks) - 1)

            net, fov, voxel_size = stack_rn_v2(
                net, num_fmaps[i], nb,
                stride1=1 if is_last_block else 2,
                activation=activation, padding=padding,
                is_training=is_training,
                use_batchnorm=use_batchnorm,
                is_first_block=is_first_block,
                use_bottleneck=use_bottleneck,
                use_dropout=use_dropout,
                make_iso=make_iso,
                merge_time_voxel_size=merge_time_voxel_size,
                name='stack' + str(i+1),
                fov=fov, voxel_size=voxel_size)

        return net, fov, voxel_size


    if resnet_size is not None:
        avail_resnet_sizes = ['18', '34', '50', '101']
        assert resnet_size in avail_resnet_sizes, \
            "unknown resnet size, choose one of: %s" % avail_resnet_sizes
        if resnet_size == '18':
            num_blocks = [2, 2,  2, 2]
            use_bottleneck = False
        elif resnet_size == '34':
            num_blocks = [3, 4,  6, 4]
            use_bottleneck = False
        elif resnet_size == '50':
            num_blocks = [3, 4,  6, 4]
            use_bottleneck = True
        elif resnet_size == '101':
            num_blocks = [3, 4, 23, 4]
            use_bottleneck = True
    else:
        assert num_blocks is not None and use_bottleneck is not None, \
            "set either resnet_size or num_blocks and use_bottleneck!"

    fov = (1, 1, 1)
    voxel_size = np.array(voxel_size[-3:])
    net = fmaps_in

    if use_conv4d:
        net = tf.expand_dims(net, 1)
        shape = net.get_shape().as_list()
        first_kernel_size = [min(shape[2], 7), 7, 7, 7]
    else:
        first_kernel_size = 7
    print("kernel first conv:", first_kernel_size)
    # x = layers.ZeroPadding2D(padding=((3, 3), (3, 3)), name='conv1_pad')(img_input)
    net, fov = conv(net, 64, first_kernel_size,
                    activation=activation,
                    padding=padding,
                    # strides=2,
                    name='conv1',
                    fov=fov, voxel_size=voxel_size)

    # x = layers.ZeroPadding2D(padding=((1, 1), (1, 1)), name='pool1_pad')(x)
    # x = layers.MaxPooling2D(3, strides=2, name='pool1_pool')(x)

    if use_batchnorm:
        net = tf.layers.batch_normalization(
            net,
            axis=1,
            training=is_training,
            epsilon=1.0001e-5,
            name='in_bn')
        logger.info("%s", net)
    net = tf.keras.activations.get(activation)(net)#, name='in_act')
    logger.info("%s", net)

    # main part
    net, fov, voxel_size = stack_fn(net, fov, voxel_size)

    if use_batchnorm:
        net = tf.layers.batch_normalization(
            net,
            axis=1,
            training=is_training,
            epsilon=1.0001e-5,
            name='out_bn')
        logger.info("%s", net)
    net = tf.keras.activations.get(activation)(net)#, name='out_act')
    logger.info("%s", net)

    net = global_average_pool(net)
    logger.info("%s", net)

    num_var = get_number_of_tf_variables()
    num_var_conv = num_var - num_var_start

    net, fov = conv(net, num_classes, 1,
                    activation=None, padding=padding,
                    name='out',
                    fov=fov, voxel_size=voxel_size)
    net = tf.reshape(net, shape=(tf.shape(net)[0], num_classes))
    logger.info("%s", net)

    num_var_end = get_number_of_tf_variables()
    num_var_fc = num_var_end - num_var
    num_var_total = num_var_end - num_var_start
    logger.info('number of variables added (conv part): %i, '
                'number of variables added (fc part): %i, '
                'number of variables added (total): %i, '
                'new total: %i',
                num_var_conv, num_var_fc, num_var_total, num_var_end)
    logger.info("field of view: %s", fov)
    logger.info("final voxel size: %s", voxel_size)

    summaries = add_summaries()

    net = tf.identity(net, name="logits")
    return net, summaries
