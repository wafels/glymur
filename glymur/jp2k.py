"""Access to JPEG2000 files.

License:  MIT
"""
import sys
if sys.hexversion >= 0x03030000:
    from contextlib import ExitStack
else:
    from contextlib2 import ExitStack
import ctypes
import math
import os
import struct
import warnings

import numpy as np

from .codestream import Codestream
from .core import progression_order
from .jp2box import Jp2kBox
from .lib import openjp2 as opj2

_cspace_map = {'rgb': opj2._CLRSPC_SRGB,
               'gray': opj2._CLRSPC_GRAY,
               'grey': opj2._CLRSPC_GRAY,
               'ycc': opj2._CLRSPC_YCC}

# Setup the default callback handlers.  See the callback functions subsection
# in the ctypes section of the Python documentation for a solid explanation of
# what's going on here.
_CMPFUNC = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p)


def _default_error_handler(msg, client_data):
    msg = "OpenJPEG library error:  {0}".format(msg.decode('utf-8').rstrip())
    opj2._set_error_message(msg)


def _default_info_handler(msg, client_data):
    print("[INFO] {0}".format(msg.decode('utf-8').rstrip()))


def _default_warning_handler(library_msg, client_data):
    library_msg = library_msg.decode('utf-8').rstrip()
    msg = "OpenJPEG library warning:  {0}".format(library_msg)
    warnings.warn(msg)

_error_callback = _CMPFUNC(_default_error_handler)
_info_callback = _CMPFUNC(_default_info_handler)
_warning_callback = _CMPFUNC(_default_warning_handler)


class Jp2k(Jp2kBox):
    """JPEG 2000 file.

    Attributes
    ----------
    filename : str
        The path to the JPEG 2000 file.
    mode : str
        The mode used to open the file.
    box : sequence
        List of top-level boxes in the file.  Each box may in turn contain
        its own list of boxes.  Will be empty if the file consists only of a
        raw codestream.
    """

    def __init__(self, filename, mode='rb'):
        """
        Parameters
        ----------
        filename : str or file
            The path to JPEG 2000 file.
        mode : str, optional
            The mode used to open the file.
        """
        self.filename = filename
        self.mode = mode
        self.box = []
        self.offset = 0

        # Parse the file for JP2/JPX contents only if we are reading it.
        if mode == 'rb':
            self._parse()

    def __str__(self):
        metadata = ['File:  ' + os.path.basename(self.filename)]
        if len(self.box) > 0:
            for box in self.box:
                metadata.append(box.__str__())
        else:
            c = self.get_codestream()
            metadata.append(c.__str__())
        return '\n'.join(metadata)

    def _parse(self):
        """Parses the JPEG 2000 file.

        Raises
        ------
        IOError
            The file was not JPEG 2000.
        """
        stat = os.stat(self.filename)
        self.length = stat.st_size
        self._file_size = stat.st_size

        with open(self.filename, 'rb') as f:

            # Make sure we have a JPEG2000 file.  It could be either JP2 or
            # J2C.  Check for J2C first, single box in that case.
            buffer = f.read(2)
            signature, = struct.unpack('>H', buffer)
            if signature == 0xff4f:
                self._codec_format = opj2._CODEC_J2K
                # That's it, we're done.  The codestream object is only
                # produced upon explicit request.
                return

            self._codec_format = opj2._CODEC_JP2

            # Should be JP2.
            # First 4 bytes should be 12, the length of the 'jP  ' box.
            # 2nd 4 bytes should be the box ID ('jP  ').
            # 3rd 4 bytes should be the box signature (13, 10, 135, 10).
            f.seek(0)
            buffer = f.read(12)
            values = struct.unpack('>I4s4B', buffer)
            L = values[0]
            T = values[1]
            signature = values[2:]
            if L != 12 or T != b'jP  ' or signature != (13, 10, 135, 10):
                msg = '{0} is not a JPEG 2000 file.'.format(self.filename)
                raise IOError(msg)

            # Back up and start again, we know we have a superbox (box of
            # boxes) here.
            f.seek(0)
            self.box = self._parse_superbox(f)

    def write(self, data, cratios=None, eph=False, psnr=None, numres=None,
              cbsize=None, psizes=None, grid_offset=None, sop=False,
              subsam=None, tilesize=None, prog=None, modesw=None,
              colorspace=None, verbose=False):
        """Write image data to a JP2/JPX/J2k file.  Intended usage of the
        various parameters follows that of OpenJPEG's opj_compress utility.

        This method can only be used to create JPEG 2000 images that can fit
        in memory.

        Parameters
        ----------
        data : array
            Image data to be written to file.
        callbacks : bool, optional
            If true, enable default info handler such that INFO messages
            produced by the OpenJPEG library are output to the console.  By
            default, OpenJPEG warning and error messages are captured by
            Python's own warning and error mechanisms.
        cbsize : tuple, optional
            Code block size (DY, DX).
        colorspace : str, optional
            Either 'rgb' or 'gray'.
        cratios : sequence, optional
            Compression ratios for successive layers.
        eph : bool, optional
            If true, write SOP marker after each header packet.
        grid_offset : tuple, optional
            Offset (DY, DX) of the origin of the image in the reference grid.
        modesw : int, optional
            Mode switch.
                1 = BYPASS(LAZY)
                2 = RESET
                4 = RESTART(TERMALL)
                8 = VSC
                16 = ERTERM(SEGTERM)
                32 = SEGMARK(SEGSYM)
        numres : int, optional
            Number of resolutions.
        prog : str, optional
            Progression order, one of "LRCP" "RLCP", "RPCL", "PCRL", "CPRL".
        psnr : list, optional
            Different PSNR for successive layers.
        psizes : list, optional
            List of precinct sizes.  Each precinct size tuple is defined in
            (height x width).
        sop : bool, optional
            If true, write SOP marker before each packet.
        subsam : tuple, optional
            Subsampling factors (dy, dx).
        tilesize : tuple, optional
            Numeric tuple specifying tile size in terms of (numrows, numcols),
            not (X, Y).
        verbose : bool, optional
            Print informational messages produced by the OpenJPEG library.

        Examples
        --------
        >>> import glymur
        >>> import pkg_resources as pkg
        >>> jfile = pkg.resource_filename(glymur.__name__, "data/nemo.jp2")
        >>> jp2 = glymur.Jp2k(jfile)
        >>> data = jp2.read(reduce=3)
        >>> from tempfile import NamedTemporaryFile
        >>> tfile = NamedTemporaryFile(suffix='.jp2', delete=False)
        >>> j = Jp2k(tfile.name, mode='wb')
        >>> j.write(data.astype(np.uint8))
        """

        cparams = opj2._set_default_encoder_parameters()

        outfile = self.filename.encode()
        n = opj2._PATH_LEN - len(outfile)
        outfile += b'0' * n
        cparams.outfile = outfile

        if self.filename[-4:].lower() == '.jp2':
            codec_fmt = opj2._CODEC_JP2
        else:
            codec_fmt = opj2._CODEC_J2K

        cparams.cod_format = codec_fmt

        # Set defaults to lossless to begin.
        cparams.tcp_rates[0] = 0
        cparams.tcp_numlayers = 1
        cparams.cp_disto_alloc = 1

        if cbsize is not None:
            w = cbsize[1]
            h = cbsize[0]
            if h * w > 4096 or h < 4 or w < 4:
                msg = "Code block area cannot exceed 4096.  "
                msg += "Code block height and width must be larger than 4."
                raise RuntimeError(msg)
            if ((math.log(h, 2) != math.floor(math.log(h, 2)) or
                 math.log(w, 2) != math.floor(math.log(w, 2)))):
                msg = "Bad code block size ({0}, {1}), "
                msg += "must be powers of 2."
                raise IOError(msg.format(h, w))
            cparams.cblockw_init = w
            cparams.cblockh_init = h

        if cratios is not None:
            cparams.tcp_numlayers = len(cratios)
            for j, cratio in enumerate(cratios):
                cparams.tcp_rates[j] = cratio
            cparams.cp_disto_alloc = 1

        if eph:
            cparams.csty |= 0x04

        if grid_offset is not None:
            cparams.image_offset_x0 = grid_offset[1]
            cparams.image_offset_y0 = grid_offset[0]

        if modesw is not None:
            for x in range(6):
                if modesw & (1 << x):
                    cparams.mode |= (1 << x)

        if numres is not None:
            cparams.numresolution = numres

        if prog is not None:
            prog = prog.upper()
            cparams.prog_order = progression_order[prog]

        if psnr is not None:
            cparams.tcp_numlayers = len(psnr)
            for j, snr_layer in enumerate(psnr):
                cparams.tcp_distoratio[j] = snr_layer
            cparams.cp_fixed_quality = 1

        if psizes is not None:
            for j, (prch, prcw) in enumerate(psizes):
                if j == 0 and cbsize is not None:
                    cblkh, cblkw = cbsize
                    if cblkh * 2 > prch or cblkw * 2 > prcw:
                        msg = "Highest Resolution precinct size must be at "
                        msg += "least twice that of the code block dimensions."
                        raise IOError(msg)
                if ((math.log(prch, 2) != math.floor(math.log(prch, 2)) or
                     math.log(prcw, 2) != math.floor(math.log(prcw, 2)))):
                    msg = "Bad precinct sizes ({0}, {1}), "
                    msg += "must be powers of 2."
                    raise IOError(msg.format(prch, prcw))

                cparams.prcw_init[j] = prcw
                cparams.prch_init[j] = prch
            cparams.csty |= 0x01
            cparams.res_spec = len(psizes)

        if sop:
            cparams.csty |= 0x02

        if subsam is not None:
            cparams.subsampling_dy = subsam[0]
            cparams.subsampling_dx = subsam[1]

        if tilesize is not None:
            cparams.cp_tdx = tilesize[1]
            cparams.cp_tdy = tilesize[0]
            cparams.tile_size_on = opj2._TRUE

        if cratios is not None and psnr is not None:
            msg = "Cannot specify cratios and psnr together."
            raise RuntimeError(msg)

        if data.ndim == 2:
            numrows, numcols = data.shape
            data = data.reshape(numrows, numcols, 1)
        elif data.ndim == 3:
            pass
        else:
            msg = "{0}D imagery is not allowed.".format(data.ndim)
            raise IOError(msg)

        numrows, numcols, num_comps = data.shape

        if colorspace is None:
            if data.shape[2] == 1 or data.shape[2] == 2:
                colorspace = opj2._CLRSPC_GRAY
            else:
                # No YCC unless specifically told to do so.
                colorspace = opj2._CLRSPC_SRGB
        else:
            if codec_fmt == opj2._CODEC_J2K:
                raise IOError('Do not specify a colorspace with J2K.')
            colorspace = colorspace.lower()
            if colorspace not in ('rgb', 'grey', 'gray'):
                msg = 'Invalid colorspace "{0}"'.format(colorspace)
                raise IOError(msg)
            elif colorspace == 'rgb' and data.shape[2] < 3:
                msg = 'RGB colorspace requires at least 3 components.'
                raise IOError(msg)
            else:
                colorspace = _cspace_map[colorspace]

        if data.dtype == np.uint8:
            comp_prec = 8
        elif data.dtype == np.uint16:
            comp_prec = 16
        else:
            raise RuntimeError("unhandled datatype")

        comptparms = (opj2._image_comptparm_t * num_comps)()
        for j in range(num_comps):
            comptparms[j].dx = cparams.subsampling_dx
            comptparms[j].dy = cparams.subsampling_dy
            comptparms[j].w = numcols
            comptparms[j].h = numrows
            comptparms[j].x0 = cparams.image_offset_x0
            comptparms[j].y0 = cparams.image_offset_y0
            comptparms[j].prec = comp_prec
            comptparms[j].bpp = comp_prec
            comptparms[j].sgnd = 0

        image = opj2._image_create(comptparms, colorspace)

        # set image offset and reference grid
        image.contents.x0 = cparams.image_offset_x0
        image.contents.y0 = cparams.image_offset_y0
        image.contents.x1 = (image.contents.x0 +
                             (numcols - 1) * cparams.subsampling_dx + 1)
        image.contents.y1 = (image.contents.y0 +
                             (numrows - 1) * cparams.subsampling_dy + 1)

        # Stage the image data to the openjpeg data structure.
        for k in range(0, num_comps):
            layer = np.ascontiguousarray(data[:, :, k], dtype=np.int32)
            dest = image.contents.comps[k].data
            src = layer.ctypes.data
            ctypes.memmove(dest, src, layer.nbytes)

        # set multi-component transform?
        if image.contents.numcomps == 3:
            cparams.tcp_mct = 1
        else:
            cparams.tcp_mct = 0

        codec = opj2._create_compress(codec_fmt)

        if verbose:
            opj2._set_info_handler(codec, _info_callback)
        else:
            opj2._set_info_handler(codec, None)

        opj2._set_warning_handler(codec, _warning_callback)
        opj2._set_error_handler(codec, _error_callback)
        opj2._setup_encoder(codec, cparams, image)
        strm = opj2._stream_create_default_file_stream_v3(self.filename, False)
        opj2._start_compress(codec, image, strm)
        opj2._encode(codec, strm)
        opj2._end_compress(codec, strm)
        opj2._stream_destroy_v3(strm)
        opj2._destroy_codec(codec)
        opj2._image_destroy(image)

        self._parse()

    def read(self, reduce=0, layer=0, area=None, tile=None, verbose=False):
        """Read a JPEG 2000 image.

        Parameters
        ----------
        layer : int, optional
            Number of quality layer to decode.
        reduce : int, optional
            Factor by which to reduce output resolution.  Use -1 to get the
            lowest resolution thumbnail.
        area : tuple, optional
            Specifies decoding image area,
            (first_row, first_col, last_row, last_col)
        tile : int, optional
            Number of tile to decode.
        verbose : bool, optional
            Print informational messages produced by the OpenJPEG library.

        Returns
        -------
        result : array
            The image data.

        Raises
        ------
        IOError
            If the image has differing subsample factors.

        Examples
        --------
        >>> import glymur
        >>> import pkg_resources as pkg
        >>> jfile = pkg.resource_filename(glymur.__name__, "data/nemo.jp2")
        >>> jp = glymur.Jp2k(jfile)
        >>> image = jp.read()
        >>> image.shape
        (1456, 2592, 3)

        Read the lowest resolution thumbnail.

        >>> thumbnail = jp.read(reduce=-1)
        >>> thumbnail.shape
        (46, 81, 3)
        """
        # Check for differing subsample factors.
        codestream = self.get_codestream(header_only=True)
        dxs = np.array(codestream.segment[1].XRsiz)
        dys = np.array(codestream.segment[1].YRsiz)
        if np.any(dxs - dxs[0]) or np.any(dys - dys[0]):
            msg = "Components must all have the same subsampling factors."
            raise IOError(msg)

        data = self._read_common(reduce=reduce,
                                 layer=layer,
                                 area=area,
                                 tile=tile,
                                 verbose=verbose,
                                 as_bands=False)

        if data.shape[2] == 1:
            data = data.view()
            data.shape = data.shape[0:2]

        return data

    def _read_common(self, reduce=0, layer=0, area=None, tile=None,
                     verbose=False, as_bands=False):
        """Read a JPEG 2000 image.

        Parameters
        ----------
        layer : int, optional
            Number of quality layer to decode.
        reduce : int, optional
            Factor by which to reduce output resolution.
        area : tuple, optional
            Specifies decoding image area,
            (first_row, first_col, last_row, last_col)
        tile : int, optional
            Number of tile to decode.
        verbose : bool, optional
            Print informational messages produced by the OpenJPEG library.
        as_bands : bool, optional
            If true, return the individual 2D components in a list.

        Returns
        -------
        data : list or array
            The individual image components or a single array.
        """
        dparam = opj2._set_default_decoder_parameters()

        infile = self.filename.encode()
        nelts = opj2._PATH_LEN - len(infile)
        infile += b'0' * nelts
        dparam.infile = infile

        dparam.decod_format = self._codec_format

        dparam.cp_layer = layer

        if reduce == -1:
            # Get the lowest resolution thumbnail.
            codestream = self.get_codestream()
            reduce = codestream.segment[2].SPcod[4]

        dparam.cp_reduce = reduce
        if area is not None:
            if area[0] < 0 or area[1] < 0:
                msg = "Upper left corner coordinates must be nonnegative:  {0}"
                msg = msg.format(area)
                raise IOError(msg)
            if area[2] <= 0 or area[3] <= 0:
                msg = "Lower right corner coordinates must be positive:  {0}"
                msg = msg.format(area)
                raise IOError(msg)
            dparam.DA_y0 = area[0]
            dparam.DA_x0 = area[1]
            dparam.DA_y1 = area[2]
            dparam.DA_x1 = area[3]

        if tile is not None:
            dparam.tile_index = tile
            dparam.nb_tile_to_decode = 1

        with ExitStack() as stack:
            stream = opj2._stream_create_default_file_stream_v3(self.filename,
                                                                True)
            stack.callback(opj2._stream_destroy_v3, stream)
            codec = opj2._create_decompress(self._codec_format)
            stack.callback(opj2._destroy_codec, codec)

            opj2._set_error_handler(codec, _error_callback)
            opj2._set_warning_handler(codec, _warning_callback)
            if verbose:
                opj2._set_info_handler(codec, _info_callback)
            else:
                opj2._set_info_handler(codec, None)

            opj2._setup_decoder(codec, dparam)
            image = opj2._read_header(stream, codec)
            stack.callback(opj2._image_destroy, image)

            if dparam.nb_tile_to_decode:
                opj2._get_decoded_tile(codec, stream, image, dparam.tile_index)
            else:
                opj2._set_decode_area(codec, image,
                                      dparam.DA_x0, dparam.DA_y0,
                                      dparam.DA_x1, dparam.DA_y1)
                opj2._decode(codec, stream, image)
                opj2._end_decompress(codec, stream)

            component = image.contents.comps[0]
            if component.sgnd:
                if component.prec <= 8:
                    dtype = np.int8
                elif component.prec <= 16:
                    dtype = np.int16
                else:
                    raise RuntimeError("Unhandled precision, datatype")
            else:
                if component.prec <= 8:
                    dtype = np.uint8
                elif component.prec <= 16:
                    dtype = np.uint16
                else:
                    raise RuntimeError("Unhandled precision, datatype")

            if as_bands:
                data = []
            else:
                nrows = image.contents.comps[0].h
                ncols = image.contents.comps[0].w
                ncomps = image.contents.numcomps
                data = np.zeros((nrows, ncols, ncomps), dtype)

            for k in range(image.contents.numcomps):
                component = image.contents.comps[k]
                nrows = component.h
                ncols = component.w

                if nrows == 0 or ncols == 0:
                    # Letting this situation continue would segfault
                    # Python.
                    msg = "Component {0} has dimensions {1} x {2}"
                    msg = msg.format(k, nrows, ncols)
                    raise IOError(msg)

                addr = ctypes.addressof(component.data.contents)
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    x = np.ctypeslib.as_array(
                        (ctypes.c_int32 * nrows * ncols).from_address(addr))
                if as_bands:
                    data.append(np.reshape(x.astype(dtype), (nrows, ncols)))
                else:
                    data[:, :, k] = np.reshape(x.astype(dtype), (nrows, ncols))

        return data

    def read_bands(self, reduce=0, layer=0, area=None, tile=None,
                   verbose=False):
        """Read a JPEG 2000 image.

        The only time you should use this method is when the image has
        different subsampling factors across components.  Otherwise you should
        use the read method.

        Parameters
        ----------
        layer : int, optional
            Number of quality layer to decode.
        reduce : int, optional
            Factor by which to reduce output resolution.
        area : tuple, optional
            Specifies decoding image area,
            (first_row, first_col, last_row, last_col)
        tile : int, optional
            Number of tile to decode.
        verbose : bool, optional
            Print informational messages produced by the OpenJPEG library.

        Returns
        -------
        lst : list
            The individual image components.

        See also
        --------
        read : read JPEG 2000 image

        Examples
        --------
        >>> import glymur
        >>> import pkg_resources as pkg
        >>> jfile = pkg.resource_filename(glymur.__name__, "data/nemo.jp2")
        >>> jp = glymur.Jp2k(jfile)
        >>> components_lst = jp.read_bands(reduce=1)
        """
        lst = self._read_common(reduce=reduce,
                                layer=layer,
                                area=area,
                                tile=tile,
                                verbose=verbose,
                                as_bands=True)

        return lst

    def get_codestream(self, header_only=True):
        """Returns a codestream object.

        Parameters
        ----------
        header_only : bool, optional
            If True, only marker segments in the main header are parsed.
            Supplying False may impose a large performance penalty.

        Returns
        -------
        Object describing the codestream syntax.

        Examples
        --------
        >>> import glymur
        >>> import pkg_resources as pkg
        >>> jfile = pkg.resource_filename(glymur.__name__, "data/nemo.jp2")
        >>> jp = glymur.Jp2k(jfile)
        >>> codestream = jp.get_codestream()
        >>> print(codestream.segment[1])
        SIZ marker segment @ (3137, 47)
            Profile:  2
            Reference Grid Height, Width:  (1456 x 2592)
            Vertical, Horizontal Reference Grid Offset:  (0 x 0)
            Reference Tile Height, Width:  (512 x 512)
            Vertical, Horizontal Reference Tile Offset:  (0 x 0)
            Bitdepth:  (8, 8, 8)
            Signed:  (False, False, False)
            Vertical, Horizontal Subsampling:  ((1, 1), (1, 1), (1, 1))

        Raises
        ------
        IOError
            If the file is JPX with more than one codestream.
        """
        with open(self.filename, 'rb') as fp:
            if self._codec_format == opj2._CODEC_J2K:
                codestream = Codestream(fp, header_only=header_only)
            else:
                box = [x for x in self.box if x.id == 'jp2c']
                if len(box) != 1:
                    msg = "JP2 files must have a single codestream."
                    raise RuntimeError(msg)
                fp.seek(box[0].offset)
                buffer = fp.read(8)
                (L, T) = struct.unpack('>I4s', buffer)
                if L == 1:
                    # Seek past the XL field.
                    buffer = fp.read(8)
                codestream = Codestream(fp, header_only=header_only)

            return codestream
