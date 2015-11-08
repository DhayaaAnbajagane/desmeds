"""
DESMEDSMaker
    - make inputs for a MEDSMaker (in the meds repo)
"""
from __future__ import print_function
import os
from os.path import basename
import numpy
from numpy import zeros, sqrt, log, vstack, array
import json
import yaml

import fitsio
import esutil as eu
import desdb

import meds
from meds.util import \
    make_wcs_positions, \
    get_meds_input_struct, \
    get_image_info_struct

from . import blacklists
from . import util

from . import util
from . import files
from .defaults import default_config

from .files import \
        TempFile, \
        StagedInFile, \
        StagedOutFile

fwhm_fac = 2*sqrt(2*log(2))

class DESMEDSMaker(dict):
    """
    generate inputs for a MEDSMaker

    parameters
    ----------
    medsconf: string or dict
        If a dict, this represents the configuration.  It must contain
        a 'medsconf' entry a minimum.  If a string, this indicates a
        DES meds configuration, which must exist at the usual place;
        see files.get_meds_config_file for details
    coadd_run: string
        Identifier for the coadd
    band: string
        Band for which to make the meds file
    check: bool, optional
        If True, check that all required files exist, default False
    do_inputs: bool, optional
        If True, write the stubby meds file holding the inputs for
        the MEDSMaker. Default True.
    do_meds: bool, optional
        If True, write the MEDS file.  Default True
    """
    def __init__(self,
                 medsconf,
                 coadd_run,
                 band,
                 check=False,
                 do_inputs=True,
                 do_meds=True):

        self.coadd_run = coadd_run
        self.band = band

        self._load_config(medsconf)

        self._set_extra_config()

        self.df = desdb.files.DESFiles()

        self.do_inputs = do_inputs
        self.do_meds = do_meds

        self.DESDATA = files.get_desdata()

        if check:
            raise NotImplementedError("implement file existence checking")

    def go(self):
        """
        make the MEDS file
        """

        if self.do_inputs:
            self._query_coadd_info()
            self._read_coadd_cat()
            self._build_image_data()
            self._build_meta_data()
            self._build_object_data()
            self._write_stubby_meds()

        if self.do_meds:
            self._load_stubby_meds()
            self._write_meds_file() # does second pass to write data

    def _read_coadd_cat(self):
        """
        read the DESDM coadd catalog, sorting by the number field (which
        should already be the case)
        """
        fname=self.df.url(type='coadd_cat',
                          coadd_run=self.coadd_run,
                          band=self.band,
                          tilename=self.tilename)
        print('reading coadd cat:',fname)
        self.coadd_cat = fitsio.read(fname, lower=True)

        # sort just in case, not needed ever AFIK
        q = numpy.argsort(self.coadd_cat['number'])
        self.coadd_cat = self.coadd_cat[q]

    def _query_coadd_info(self):
        """
        query the DES database to get Coadd information, including
        the source list
        """
        print('getting coadd info and source list')
        self.conn = desdb.Connection()
        self.cf = desdb.files.Coadd(coadd_run=self.coadd_run,
                                    band=self.band,
                                    conn=self.conn)
        self.cf.load(srclist=True)

        self.tilename=self.cf['tilename']

    def _build_image_data(self):
        """
        build up the image paths, wcs strings, etc. for each
        coadd and coadd source image
        """
        print('building information for each image source')

        srclist = self._get_srclist()
        wcs_json = self._get_wcs_json(srclist)

        impath=self.cf['image_url'].replace(self.DESDATA,'${DESDATA}')
        segpath=self.cf['seg_url'].replace(self.DESDATA,'${DESDATA}')

        # build data
        image_info = self._get_image_info_struct(srclist,wcs_json)

        # assume all are the same
        image_info['position_offset'] = self['position_offset']

        ind = 0
        image_info['image_id'][ind] = self.cf['image_id']
        image_info['image_path'][ind] = impath
        image_info['image_ext'][ind] = self['coadd_image_ext']

        # weight is in same file as image
        image_info['weight_path'][ind] = impath
        image_info['weight_ext'][ind] = self['coadd_weight_ext']

        # bmask is in same file as image, or none for coadd
        # don't set bmask for coadd
        image_info['bmask_path'][ind] = ''
        image_info['bmask_ext'][ind] = -1

        # don't set for coadd, leave empty string
        image_info['bkg_path'][ind] = ''
        image_info['bkg_ext'][ind] = -1

        image_info['seg_path'][ind] = segpath
        image_info['seg_ext'][ind] = self['coadd_seg_ext']

        image_info['wcs'][ind] = wcs_json[ind]
        image_info['magzp'][ind] = self.cf['magzp']
        image_info['scale'][ind] = self._get_scale(self.cf['magzp'])

        ind = 1
        for s in srclist:
            impath=s['red_image'].replace(self.DESDATA,'${DESDATA}')
            skypath=s['red_bkg'].replace(self.DESDATA,'${DESDATA}')
            segpath=s['red_seg'].replace(self.DESDATA,'${DESDATA}')

            image_info['image_id'][ind] = s['id']
            image_info['image_flags'][ind] = s['flags']

            # for DES, image, weight, bmask all in same file
            image_info['image_path'][ind]  = impath
            image_info['image_ext'][ind]  = self['se_image_ext']

            image_info['weight_path'][ind] = impath
            image_info['weight_ext'][ind]  = self['se_weight_ext']

            image_info['bmask_path'][ind]  = impath
            image_info['bmask_ext'][ind]  = self['se_bmask_ext']

            # background map and seg map in different files
            image_info['bkg_path'][ind] = skypath
            image_info['bkg_ext'][ind] = self['se_bkg_ext']
            image_info['seg_path'][ind] = segpath
            image_info['seg_ext'][ind] = self['se_seg_ext']

            image_info['wcs'][ind] = wcs_json[ind]
            image_info['magzp'][ind] = s['magzp']
            image_info['scale'][ind] = self._get_scale(s['magzp'])
            ind += 1

        self.image_info = image_info

    def _get_scale(self, magzp):
        """
        get the scale factor required to put the image on the
        reference zero point
        """
        scale = 10.0**( 0.4*(self['magzp_ref']-magzp) )
        return scale

    def _get_image_info_struct(self,srclist,wcs_json):
        """
        build the data type for the image info structure. We use
        the maximum string size rather than variable length strings
        """
        nsrc = len(srclist)
        slen = len(self.cf['image_url'].replace(self.DESDATA,'${DESDATA}'))
        for s in srclist:
            slen = max(slen,
                       len(s['red_image'].replace(self.DESDATA,'${DESDATA}')),
                       len(s['red_bkg'].replace(self.DESDATA,'${DESDATA}')),
                       len(s['red_seg'].replace(self.DESDATA,'${DESDATA}')))
        #sfmt = 'S%d' % slen

        wcs_len = reduce(lambda x,y: max(x,len(y)),wcs_json,0)

        return get_image_info_struct(nsrc+1, slen, wcs_len=wcs_len)

    def _get_wcs_json(self,srclist):
        """
        get string versions of the wcs for each image
        """
        coadd_wcs = fitsio.read_header(self.cf['image_url'],
                                       ext=self['coadd_image_ext'])
        wcs_json = []
        wcs_json.append(json.dumps(util.fitsio_header_to_dict(coadd_wcs)))
        for s in srclist:
            wcs_json.append(json.dumps(s['wcs_header']))
        return wcs_json

    def _get_srclist(self):
        """
        set the srclist, checking possibly for redone astrometry.
        also check against blacklist
        """
        srclist = self.cf.srclist

        # do blacklists
        for s in srclist:
            s['flags'] = 0
        blacklists.add_bigind(srclist)
        blacklists.add_blacklist_flags(srclist)

        # read astrom header
        for s in srclist:
            img_hdr = fitsio.read_header(s['red_image'],
                                         ext=self['se_image_ext'])
            if self['use_astro_refine']:
                wcs_hdr = fitsio.read_scamp_head(s['astro_refine'])
                wcs_hdr = util.add_naxis_to_fitsio_header(wcs_hdr,img_hdr)
            else:
                wcs_hdr = img_hdr

            s['wcs_header'] = util.fitsio_header_to_dict(wcs_hdr)

        return srclist

    def _get_meta_data_dtype(self,cfg):
        """
        get the metadata data type
        """
        dt = [('magzp_ref','f8'),
              ('DESDATA','S%d' % len('${DESDATA}')),
              ('medsconf','S%d' % len(self['medsconf'])),
              ('config','S%d' % len(cfg))]
        return dt

    def _build_meta_data(self):
        """
        create the mdata data structure and copy in some information
        """
        print('building meta data')
        cfg = {}
        cfg.update(self)
        cfg = yaml.dump(cfg)
        dt = self._get_meta_data_dtype(cfg)
        meta_data = zeros(1,dtype=dt)
        meta_data['magzp_ref'] = self['magzp_ref']
        meta_data['DESDATA'] = '${DESDATA}'
        meta_data['medsconf'] = self['medsconf']
        meta_data['config'] = cfg
        self.meta_data = meta_data

    def _get_coadd_objects_ids(self):
        """
        query the des database to get the unique identifier
        for each object
        """
        # do queries to get coadd object ids        
        qwry = """
        select
            coadd_objects_id,
            object_number
        from
            coadd_objects
        where
            COADD_OBJECTS.imageid_{band} = {id}
        """
        qwry = qwry.format(band=self.band,id=self.cf['image_id'])
        return self.conn.quick(qwry,array=True)

    def _get_box_sizes(self):
        """
        get box sizes that are wither 2**N or 3*2**N, within
        the limits set by the user
        """
        cat = self.coadd_cat

        sigma_size = self._get_sigma_size()

        # now do row and col sizes
        row_size = cat['ymax_image'] - cat['ymin_image'] + 1
        col_size = cat['xmax_image'] - cat['xmin_image'] + 1

        # get max of all three
        box_size = vstack((col_size,row_size,sigma_size)).max(axis=0)

        # clip to range
        box_size = box_size.clip(self['min_box_size'],self['max_box_size'])

        # now put in fft sizes
        bins = [0]
        bins.extend([sze for sze in self['allowed_box_sizes'] 
                     if sze >= self['min_box_size']
                     and sze <= self['max_box_size']])

        if bins[-1] != self['max_box_size']:
            bins.append(self['max_box_size'])

        bin_inds = numpy.digitize(box_size,bins,right=True)
        bins = array(bins)

        return bins[bin_inds]

    def _get_sigma_size(self):
        """
        "sigma" size, based on flux radius and ellipticity
        """
        cat = self.coadd_cat

        ellipticity = 1.0 - cat['b_world']/cat['a_world']
        sigma = cat['flux_radius']*2.0/fwhm_fac
        drad = sigma*self['sigma_fac']
        drad = drad*(1.0 + ellipticity)
        drad = numpy.ceil(drad)
        sigma_size = 2*drad.astype('i4') # sigma size is twice the radius

        return sigma_size

    def _build_object_data(self):
        """
        make the object data such as box sizes and ra,dec based on
        the row,col->ra,dec transformation
        """
        print('building basic object data')

        nobj=len(self.coadd_cat)

        extra_fields=self['extra_obj_data_fields']
        self.obj_data = get_meds_input_struct(nobj,
                                              extra_fields=extra_fields)

        self.obj_data['number'] = self.coadd_cat['number']

        input_row = self.coadd_cat[self['row_name']]
        input_col = self.coadd_cat[self['col_name']]

        pos=self._make_wcs_positions(input_row, input_col)
        self.obj_data['input_row'] = pos['zrow']
        self.obj_data['input_col'] = pos['zcol']

        # required
        self.obj_data['box_size'] = self._get_box_sizes()

        # do coadd ids and check things
        iddata = self._get_coadd_objects_ids()
        q = numpy.argsort(iddata['object_number'])
        iddata = iddata[q]
        mess="Could not find all objects in DESDM table!"
        check=numpy.array_equal(self.coadd_cat['number'],iddata['object_number'])
        assert check,mess

        # required
        self.obj_data['id'] = iddata['coadd_objects_id']

        # get ra,dec
        coadd_wcs_dict = json.loads(self.image_info['wcs'][0])
        coadd_wcs = eu.wcsutil.WCS(coadd_wcs_dict)
        # swapped plus 1 to account for x,y -> col,row and sextractor +1's

        ra,dec = coadd_wcs.image2sky(pos['wcs_col'], pos['wcs_row'])
        self.obj_data['ra'] = ra
        self.obj_data['dec'] = dec

    def _write_stubby_meds(self):
        """
        Store the inputs to the MEDSMaker in a "stubby" MEDS file,
        with fewer columns for object_data than a full MEDS
        """
        stubby_path = self._get_stubby_path()
        print('writing stubby meds file:',stubby_path)

        tmpdir = files.get_temp_dir()
        with StagedOutFile(stubby_path,tmpdir=tmpdir) as sf:
            with fitsio.FITS(sf.path,'rw',clobber=True) as f:
                f.write(self.obj_data, extname='object_data')
                f.write(self.image_info, extname='image_info')
                f.write(self.meta_data, extname='metadata')

    def _load_stubby_meds(self):
        """
        load the meds input data stored in a multi-extension fits file
        and store data as attributes in self
        """
        stubby_path = self._get_stubby_path()

        print('reading stubby meds:',stubby_path)
        tmpdir=files.get_temp_dir()
        with StagedInFile(stubby_path,tmpdir=tmpdir) as sf:
            with fitsio.FITS(sf.path,'r') as f:
                self.obj_data = f['object_data'].read()
                self.image_info = f['image_info'].read()
                self.meta_data = f['metadata'].read()

    def _get_stubby_path(self):
        """
        file to hold input to the MEDSMaker
        """
        stubby_path = files.get_meds_stubby_file(self['medsconf'],
                                                 self.coadd_run,
                                                 self.tilename,
                                                 self.band)
        return stubby_path

    def _write_meds_file(self):
        """
        write the data using the MEDSMaker
        """

        self.maker=meds.MEDSMaker(self.obj_data,
                                  self.image_info,
                                  config=self,
                                  meta_data=self.meta_data)

        ucfilename = self._get_meds_filename('uncompressed-temp')
        fzfilename = self._get_meds_filename('compressed-final')

        with TempFile(ucfilename) as tfile:
            self.maker.write(tfile.path)
            self._compress_meds_file(tfile.path, fzfilename)


    def _compress_meds_file(self, ucfilename, fzfilename):
        """
        run fpack on the file

        parameters
        ----------
        ucfilename: string
            filename for the uncompressed file
        fzfilename: string
            filename for the compressed file
        """

        tup=(basename(ucfilename),basename(fzfilename))
        print('compressing file: %s -> %s' % tup)
        tpath=files.expandpath(fzfilename)
        if os.path.exists(tpath):
            os.remove(tpath)

        tmpdir = files.get_temp_dir()
        with StagedOutFile(fzfilename,tmpdir=tmpdir) as sf:
            cmd = self['fpack_command']
            cmd = cmd.format(fname=ucfilename)
            ret=os.system(cmd)

            if ret != 0:
                raise RuntimeError("failed to compress file")

        print('output is in:',fzfilename)

    def _get_meds_filename(self, type, compressed=False):
        """
        the uncompressed file is written to a temporary directory
        """


        if type=='uncompressed-temp':
            dir = files.get_temp_dir()
            ext='fits'
        elif type=='compressed-final':
            ext='fits.fz'
            dir=None
        else:
            raise RuntimeError("type should be 'uncompressed-temp' "
                               "or 'compressed-final'")

        filename = files.get_meds_file(self['medsconf'],
                                       self.coadd_run,
                                       self.tilename,
                                       self.band,
                                       ext=ext)
        if dir is not None:
            bname = basename(filename)
            filename = os.path.join(dir, bname)

        return filename


    def _make_wcs_positions(self, row, col, inverse=False):
        """
        get a structure holding the original positions
        and offset ones
        """

        pos = make_wcs_positions(row,
                                 col,
                                 self['position_offset'],
                                 inverse=inverse)
        return pos

    def _load_config(self, medsconf):
        """
        load the default config, then load the input config
        """

        self.update(default_config)

        if isinstance(medsconf, dict):
            conf=medsconf
        else:
            conf=files.read_meds_config(medsconf)
            conf['medsconf'] = medsconf

        util.check_for_required_config(conf, ['medsconf'])

        self.update(conf)


    def _set_extra_config(self):
        """
        set extra configuration parameters
        """

        # to be saved for posterity
        self['coadd_run'] = self.coadd_run
        self['band'] = self.band

        # these are not required obj_data fields
        self['extra_obj_data_fields'] = [
            ('number','i8'),
            ('input_row','f8'),
            ('input_col','f8'),
        ]

        # for converting fwhm and sigma for a gaussian
        self['fpack_command'] = \
            'fpack -t %d,%d {fname}' % tuple(self['fpack_dims'])